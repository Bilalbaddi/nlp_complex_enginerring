from app.core.pii import mask_name, mask_phone, strip_pii


def test_mask_name_and_phone() -> None:
    assert mask_name("Rajesh Kulkarni") == "R***** K*******"
    assert mask_name(None) is None and mask_name("") == ""
    assert mask_phone("9876543210") == "******3210"
    assert mask_phone("+91 98765 43210") == "********3210"
    assert mask_phone(None) is None


def test_strip_pii_removes_contact_details() -> None:
    text = "Call me on 9876543210 or +91-98765-43210, mail rajesh.k@example.com, Aadhaar 1234 5678 9012, PAN ABCDE1234F."
    out = strip_pii(text)
    for leaked in ("9876543210", "98765-43210", "rajesh.k@example.com", "1234 5678 9012", "ABCDE1234F"):
        assert leaked not in out
    assert "[PHONE]" in out and "[EMAIL]" in out and out.count("[ID]") == 2


def test_strip_pii_known_names_and_plain_text_untouched() -> None:
    assert strip_pii("Rajesh Kulkarni reported a pothole", known_names=["Rajesh Kulkarni"]) == "[NAME] reported a pothole"
    plain = "Pothole on MG Road near ward 7 for 12 days"
    assert strip_pii(plain) == plain
