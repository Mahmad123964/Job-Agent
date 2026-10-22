import base64
import logging
import mimetypes
import re
from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class Email:
    def __init__(self, raw: dict):
        self.id: str = raw["id"]
        self.thread_id: str = raw["threadId"]
        self.internal_date: int = int(raw.get("internalDate", 0))
        self.label_ids: list[str] = raw.get("labelIds", [])
        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        self.from_header: str = headers.get("from", "")
        self.sender_name, self.sender_email = parseaddr(self.from_header)
        self.subject: str = headers.get("subject", "(no subject)")
        self.message_id: str = headers.get("message-id", "")
        self.body: str = _extract_body(raw.get("payload", {}))
        # Raw HTML part (if any) so link hrefs survive; `body` is text only.
        self.html: str = _extract_html(raw.get("payload", {}))


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    plain, html = [], []

    def walk(part: dict):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            html.append(_b64(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    if html:
        return html_to_text("\n".join(html))
    return ""


def _extract_html(payload: dict) -> str:
    html = []

    def walk(part: dict):
        data = part.get("body", {}).get("data")
        if data and part.get("mimeType", "") == "text/html":
            html.append(_b64(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(html)


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class Gmail:
    def __init__(self, credentials_file: Path, token_file: Path):
        creds: Optional[Credentials] = None
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not credentials_file.exists():
                    raise SystemExit(f"Missing {credentials_file}. Download an OAuth Desktop client secret from Google Cloud Console.")
                creds = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES).run_local_server(port=0)
            token_file.write_text(creds.to_json(), encoding="utf-8")
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._label_cache: dict[str, str] = {}

    # ---- labels -------------------------------------------------------
    def label_id(self, name: str) -> str:
        if not self._label_cache:
            for lab in self.svc.users().labels().list(userId="me").execute().get("labels", []):
                self._label_cache[lab["name"]] = lab["id"]
        if name not in self._label_cache:
            body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            lab = self.svc.users().labels().create(userId="me", body=body).execute()
            self._label_cache[name] = lab["id"]
        return self._label_cache[name]

    def add_labels(self, msg_id: str, names: list[str]) -> None:
        ids = [self.label_id(n) for n in names]
        self.svc.users().messages().modify(userId="me", id=msg_id, body={"addLabelIds": ids}).execute()

    # ---- reading ------------------------------------------------------
    def search(self, query: str, limit: int = 25) -> list[str]:
        resp = self.svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get(self, msg_id: str) -> Email:
        return Email(self.svc.users().messages().get(userId="me", id=msg_id, format="full").execute())

    def thread_messages(self, thread_id: str) -> list[Email]:
        t = self.svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        return [Email(m) for m in t.get("messages", [])]

    # ---- writing ------------------------------------------------------
    @staticmethod
    def _build(to: str, subject: str, body: str, attachments: list[Path] = (), reply_to: Optional[Email] = None) -> EmailMessage:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to and reply_to.message_id:
            msg["In-Reply-To"] = reply_to.message_id
            msg["References"] = reply_to.message_id
        msg.set_content(body)
        for path in attachments:
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        return msg

    @staticmethod
    def _raw(msg: EmailMessage) -> str:
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _create_draft(self, msg: EmailMessage, thread_id: Optional[str] = None) -> dict:
        message: dict = {"raw": self._raw(msg)}
        if thread_id:
            message["threadId"] = thread_id
        draft = self.svc.users().drafts().create(userId="me", body={"message": message}).execute()
        return {"draft_id": draft["id"], "message_id": draft["message"]["id"], "url": f"https://mail.google.com/mail/u/0/#drafts/{draft['message']['id']}"}

    def create_reply_draft(self, original: Email, body: str, attachments: list[Path]) -> dict:
        """Create a draft replying to the *literal* sender address of `original`."""
        subject = original.subject if original.subject.lower().startswith("re:") else f"Re: {original.subject}"
        msg = self._build(original.sender_email, subject, body, attachments, reply_to=original)
        return self._create_draft(msg, original.thread_id)

    def create_draft(self, to: str, subject: str, body: str, attachments: list[Path]) -> dict:
        """Create a fresh (non-reply) draft to `to`. Used by the link resolver when the
        recruiter address came from a job page rather than an inbound email."""
        return self._create_draft(self._build(to, subject, body, attachments))

    def send_draft(self, draft_id: str) -> dict:
        return self.svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()

    def draft_exists(self, draft_id: str) -> bool:
        try:
            self.svc.users().drafts().get(userId="me", id=draft_id, format="minimal").execute()
            return True
        except Exception:  # noqa: BLE001
            return False

    def send_mail(self, to: str, subject: str, body: str) -> dict:
        msg = self._build(to, subject, body)
        return self.svc.users().messages().send(userId="me", body={"raw": self._raw(msg)}).execute()
