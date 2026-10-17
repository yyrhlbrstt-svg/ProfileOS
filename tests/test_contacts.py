"""Customer details read the way they arrive."""

from __future__ import annotations

from profileos.projects.contacts import (
    email_problem,
    find_duplicates,
    format_phone,
    parse_contact,
    phone_problem,
    search_customers,
)
from profileos.projects.model import Customer


def _customer(**fields) -> Customer:
    fields.setdefault("customer_id", "C-0001")
    fields.setdefault("name", "משה כהן")
    return Customer(**fields)


class TestPhone:
    def test_numbers_are_written_one_way(self):
        assert format_phone("0541234567") == "054-123-4567"
        assert format_phone("+972-54-123-4567") == "054-123-4567"
        assert format_phone("02 997 3510") == "02-997-3510"
        assert format_phone("077-1234567") == "077-123-4567"

    def test_what_is_not_a_number_is_left_as_typed(self):
        assert format_phone("  בערב  ") == "בערב"

    def test_problems_are_named(self):
        assert phone_problem("") == ""
        assert phone_problem("054-123-4567") == ""
        assert "חסרות" in phone_problem("054-12")
        assert phone_problem("1234567890")

    def test_email(self):
        assert email_problem("") == ""
        assert email_problem("a@b.co.il") == ""
        assert email_problem("a@b")


class TestPasting:
    def test_a_whatsapp_contact(self):
        parsed = parse_contact("משה כהן\n+972 54-123-4567")
        assert parsed.name == "משה כהן"
        assert parsed.phone == "054-123-4567"

    def test_an_email_signature(self):
        parsed = parse_contact(
            "בברכה,\nיוסי לוי\nלוי בנייה בע\"מ\nנייד: 052-7654321 | משרד: 02-9973510\n"
            "yossi@levi-build.co.il\nרחוב הזית 5, בית אל\nח.פ. 515123456"
        )
        assert parsed.phone == "052-765-4321"
        assert parsed.extra_phones == ["02-997-3510"]
        assert parsed.email == "yossi@levi-build.co.il"
        assert parsed.city == "בית אל"
        assert "הזית 5" in parsed.address
        assert parsed.tax_id == "515123456"
        assert parsed.name == "יוסי לוי"

    def test_one_line_read_out_over_the_phone(self):
        parsed = parse_contact("דוד ישראלי, 0501112233, עפרה")
        assert parsed.name == "דוד ישראלי"
        assert parsed.phone == "050-111-2233"
        assert parsed.city == "עפרה"

    def test_nothing_is_invented_from_nothing(self):
        assert parse_contact("").as_dict() == {}
        assert parse_contact("   ").as_dict() == {}


class TestDuplicates:
    def test_the_same_phone_is_the_same_person(self):
        book = [_customer(phone="054-123-4567")]
        assert find_duplicates(book, name="מוישה", phone="+972541234567")

    def test_the_same_name_in_another_order(self):
        assert find_duplicates([_customer()], name="כהן משה")

    def test_a_customer_is_not_a_duplicate_of_themselves(self):
        assert not find_duplicates([_customer()], name="משה כהן", ignore_id="C-0001")

    def test_different_people_are_different(self):
        assert not find_duplicates([_customer(phone="054-123-4567")],
                                   name="דוד לוי", phone="052-000-0000")


def test_search_by_any_part_of_a_number_or_a_place():
    book = [_customer(phone="054-123-4567", city="בית אל"),
            _customer(customer_id="C-0002", name="דוד לוי", city="עפרה")]
    assert [c.name for c in search_customers(book, "4567")] == ["משה כהן"]
    assert [c.name for c in search_customers(book, "עפרה")] == ["דוד לוי"]
    assert len(search_customers(book, "")) == 2
