#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import mimetypes
import re
import signal
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from playwright.async_api import BrowserContext, Page, Request, Response, async_playwright

XHR_TYPES = {"xhr", "fetch"}
TEXTUAL_CONTENT_TYPE_RE = re.compile(
    r"(^text/)|(" 
    r"application/(json|javascript|xml|x-www-form-urlencoded|graphql)|" 
    r"\+json$|\+xml$)",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RequestRecord:
    id: str
    started_at: str
    page_url: str
    resource_type: str
    method: str
    url: str
    request_headers: Dict[str, str]
    request_post_data: Optional[str]
    timing: Optional[Dict[str, Any]] = None
    frame_url: Optional[str] = None


@dataclass
class CaptureConfig:
    output_dir: Path
    include_response_body: bool = True
    body_preview_limit: int = 5000
    save_binary_body: bool = True


@dataclass
class CaptureSession:
    config: CaptureConfig
    records_file: Path = field(init=False)
    bodies_dir: Path = field(init=False)
    pending_requests: Dict[Request, RequestRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.bodies_dir = self.config.output_dir / "bodies"
        self.bodies_dir.mkdir(parents=True, exist_ok=True)
        self.records_file = self.config.output_dir / "xhr_records.jsonl"

    def start_request(self, request: Request) -> None:
        frame_url = None
        try:
            frame_url = request.frame.url
        except Exception:
            frame_url = None

        record = RequestRecord(
            id=str(uuid.uuid4()),
            started_at=utc_now_iso(),
            page_url=request.frame.page.url if request.frame and request.frame.page else "",
            resource_type=request.resource_type,
            method=request.method,
            url=request.url,
            request_headers=request.headers,
            request_post_data=request.post_data,
            timing=request.timing,
            frame_url=frame_url,
        )
        self.pending_requests[request] = record

    async def complete_response(self, response: Response) -> None:
        request = response.request
        base = self.pending_requests.pop(request, None)
        if base is None:
            base = RequestRecord(
                id=str(uuid.uuid4()),
                started_at=utc_now_iso(),
                page_url="",
                resource_type=request.resource_type,
                method=request.method,
                url=request.url,
                request_headers=request.headers,
                request_post_data=request.post_data,
                timing=request.timing,
            )

        response_headers = response.headers
        content_type = response_headers.get("content-type", "")

        body_path: Optional[str] = None
        response_body_preview: Optional[str] = None
        response_body_base64: Optional[str] = None
        response_body_error: Optional[str] = None

        if self.config.include_response_body:
            body_bytes, body_error = await self._read_response_body(response)
            if body_error:
                response_body_error = body_error
            elif body_bytes is not None:
                body_path, response_body_preview, response_body_base64 = self._store_body(
                    base.id, body_bytes, content_type
                )

        finished_at = utc_now_iso()
        record = {
            "id": base.id,
            "started_at": base.started_at,
            "finished_at": finished_at,
            "duration_ms": self._duration_ms(base.started_at, finished_at),
            "page_url": base.page_url,
            "frame_url": base.frame_url,
            "resource_type": base.resource_type,
            "method": base.method,
            "url": base.url,
            "request": {
                "headers": base.request_headers,
                "post_data": base.request_post_data,
                "timing": base.timing,
            },
            "response": {
                "status": response.status,
                "status_text": response.status_text,
                "ok": response.ok,
                "headers": response_headers,
                "content_type": content_type,
                "body_file": body_path,
                "body_preview": response_body_preview,
                "body_base64": response_body_base64,
                "body_error": response_body_error,
            },
        }

        self._append_record(record)
        print(f"[XHRCapture] {record['method']} {record['url']} -> {response.status}")

    def fail_request(self, request: Request) -> None:
        base = self.pending_requests.pop(request, None)
        failure = request.failure

        if base is None:
            base = RequestRecord(
                id=str(uuid.uuid4()),
                started_at=utc_now_iso(),
                page_url="",
                resource_type=request.resource_type,
                method=request.method,
                url=request.url,
                request_headers=request.headers,
                request_post_data=request.post_data,
                timing=request.timing,
            )

        finished_at = utc_now_iso()
        record = {
            "id": base.id,
            "started_at": base.started_at,
            "finished_at": finished_at,
            "duration_ms": self._duration_ms(base.started_at, finished_at),
            "page_url": base.page_url,
            "frame_url": base.frame_url,
            "resource_type": base.resource_type,
            "method": base.method,
            "url": base.url,
            "request": {
                "headers": base.request_headers,
                "post_data": base.request_post_data,
                "timing": base.timing,
            },
            "response": {
                "status": None,
                "status_text": None,
                "ok": False,
                "headers": {},
                "content_type": None,
                "body_file": None,
                "body_preview": None,
                "body_base64": None,
                "body_error": failure,
            },
        }
        self._append_record(record)
        print(f"[XHRCapture] FAILED {record['method']} {record['url']} -> {failure}")

    async def _read_response_body(self, response: Response) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            body = await response.body()
            return body, None
        except Exception as ex:
            return None, str(ex)

    def _store_body(
        self, request_id: str, body_bytes: bytes, content_type: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        is_textual = bool(TEXTUAL_CONTENT_TYPE_RE.search(content_type))

        if is_textual:
            try:
                text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = body_bytes.decode("utf-8", errors="replace")
            preview = text[: self.config.body_preview_limit]
            return None, preview, None

        if self.config.save_binary_body:
            ext = self._guess_ext(content_type)
            file_name = f"{request_id}{ext}"
            file_path = self.bodies_dir / file_name
            file_path.write_bytes(body_bytes)
            return str(file_path), None, None

        return None, None, base64.b64encode(body_bytes).decode("ascii")

    @staticmethod
    def _guess_ext(content_type: str) -> str:
        mime = content_type.split(";", 1)[0].strip().lower()
        if not mime:
            return ".bin"
        ext = mimetypes.guess_extension(mime)
        return ext or ".bin"

    @staticmethod
    def _duration_ms(started_at: str, finished_at: str) -> Optional[float]:
        try:
            start_dt = datetime.fromisoformat(started_at)
            end_dt = datetime.fromisoformat(finished_at)
            return round((end_dt - start_dt).total_seconds() * 1000, 3)
        except Exception:
            return None

    def _append_record(self, record: Dict[str, Any]) -> None:
        with self.records_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def attach_page_listeners(page: Page, session: CaptureSession) -> None:
    page.on(
        "request",
        lambda request: session.start_request(request)
        if request.resource_type in XHR_TYPES
        else None,
    )
    page.on(
        "response",
        lambda response: asyncio.create_task(session.complete_response(response))
        if response.request.resource_type in XHR_TYPES
        else None,
    )
    page.on(
        "requestfailed",
        lambda request: session.fail_request(request)
        if request.resource_type in XHR_TYPES
        else None,
    )


def register_context_listeners(context: BrowserContext, session: CaptureSession) -> None:
    for page in context.pages:
        attach_page_listeners(page, session)

    context.on("page", lambda page: attach_page_listeners(page, session))


async def run_capture(args: argparse.Namespace) -> None:
    session = CaptureSession(
        config=CaptureConfig(
            output_dir=args.output,
            include_response_body=not args.no_body,
            body_preview_limit=args.body_preview_limit,
            save_binary_body=not args.no_binary_body,
        )
    )

    stop_event = asyncio.Event()

    def _signal_stop(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: _signal_stop())

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        context = await browser.new_context(ignore_https_errors=args.ignore_https_errors)
        register_context_listeners(context, session)

        page = await context.new_page()
        attach_page_listeners(page, session)

        if args.url:
            await page.goto(args.url)

        print("\nXHRCapture is running.")
        print(f"Output file: {session.records_file}")
        print("Press Ctrl+C to stop capturing.\n")

        await stop_event.wait()

        await context.close()
        await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture XHR/fetch network traffic while browsing a website."
    )
    parser.add_argument(
        "--url",
        default="https://example.com",
        help="Initial URL to open in the browser.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output") / datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Directory where capture files will be written.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser without UI.",
    )
    parser.add_argument(
        "--ignore-https-errors",
        action="store_true",
        help="Ignore TLS/SSL certificate errors.",
    )
    parser.add_argument(
        "--no-body",
        action="store_true",
        help="Do not capture response body.",
    )
    parser.add_argument(
        "--no-binary-body",
        action="store_true",
        help="Do not save non-text response body to files; store as base64 in JSONL instead.",
    )
    parser.add_argument(
        "--body-preview-limit",
        type=int,
        default=5000,
        help="Maximum characters for textual response body preview.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_capture(args))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
