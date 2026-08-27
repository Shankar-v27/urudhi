from urudhi.config import format_presence_report, presence_report


def test_report_shows_presence_never_values():
    env = {
        "RAZORPAY_KEY_ID": "rzp_test_abc123", "RAZORPAY_KEY_SECRET": "s3cr3t-value",
        "RAZORPAY_WEBHOOK_SECRET": "whsec-value", "ANTHROPIC_API_KEY": "sk-example-value",
        "ANTHROPIC_BASE_URL": "https://api.llmsrelay.com", "ANTHROPIC_MODEL": "claude-sonnet-5",
        "URUDHI_API_TOKEN": "token-value",
    }
    text = format_presence_report(env)
    for secret in ("s3cr3t-value", "whsec-value", "sk-example-value", "token-value", "abc123"):
        assert secret not in text
    assert "Razorpay Key ID         : configured (test mode)" in text
    assert "Razorpay Webhook Secret : configured" in text
    assert "Claude Base URL         : https://api.llmsrelay.com" in text
    assert "Claude Model            : claude-sonnet-5" in text


def test_missing_and_live_keys_are_called_out():
    rows = dict(presence_report({"RAZORPAY_KEY_ID": "rzp_live_xyz"}))
    assert rows["Razorpay Key ID"].startswith("configured (NOT a test key")
    assert rows["Razorpay Key Secret"] == "missing"
    assert rows["Claude API"] == "missing"
    assert dict(presence_report({}))["Razorpay Key ID"] == "missing"
