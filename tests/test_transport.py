from email import message_from_bytes
from email.policy import default as email_policy

import pytest

from urudhi.ledger.models import Channel, Debtor
from urudhi.transport.email import EmailOutbox, reference_from_subject


@pytest.fixture
def debtor():
    return Debtor(id="deb_1", name="Kumar Textiles", contact_name="Kumar",
                  phone="+919800000001", email="kumar@example.in", preferred_channel=Channel.EMAIL)


class TestSandbox:
    def test_writes_a_real_eml_and_returns_a_message_id(self, tmp_path, debtor):
        outbox = EmailOutbox("sandbox", directory=tmp_path)
        message_id = outbox.send(debtor, Channel.EMAIL, "Namaste Kumar, ₹2,500 is overdue.\nReply STOP to opt out.",
                                 subject="Invoice URU/2026/0001 — payment reminder [URU/2026/0001]",
                                 reference="inv_1")
        files = list(tmp_path.glob("*.eml"))
        assert len(files) == 1 and message_id.startswith("<")
        parsed = message_from_bytes(files[0].read_bytes(), policy=email_policy)
        assert parsed["To"] == "Kumar <kumar@example.in>"
        assert parsed["X-Urudhi-Invoice"] == "inv_1"
        assert "[URU/2026/0001]" in parsed["Subject"]
        assert "STOP" in parsed.get_content()

    def test_mode_is_explicit(self, tmp_path):
        assert EmailOutbox("sandbox", directory=tmp_path).mode == "sandbox"
        with pytest.raises(ValueError, match="SMTP_HOST"):
            EmailOutbox("smtp", directory=tmp_path)
        with pytest.raises(ValueError, match="unknown email mode"):
            EmailOutbox("carrier-pigeon", directory=tmp_path)

    def test_from_env(self, tmp_path):
        outbox = EmailOutbox.from_env({"URUDHI_EMAIL_MODE": "sandbox", "URUDHI_OUTBOX_DIR": str(tmp_path)})
        assert outbox.mode == "sandbox"


class TestInboundMatching:
    def test_reference_from_subject(self):
        assert reference_from_subject("Re: Invoice URU/2026/0001 — reminder [URU/2026/0001]") == "URU/2026/0001"
        assert reference_from_subject("hello") is None
        assert reference_from_subject("") is None
