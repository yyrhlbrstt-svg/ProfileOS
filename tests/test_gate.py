"""Access-gate tests.

The gate is the one part of the suite whose failure mode is somebody else
using the shop's software. So the tests are mostly attempts to get past it:
without the key, with a copied key on another machine, with the right password
and wrong answers, and by guessing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from profileos.security.gate import (
    AccessDenied,
    Gate,
    NotEnrolled,
    Operator,
    find_key_files,
    normalise_answer,
    password_problems,
    read_key_file,
    write_key_file,
)
from profileos.security.hwid import HardwareFingerprint, Trait

PASSWORD = "Sulam-Yaakov-1-BeitEl"
QUESTIONS = [
    ("שם הרחוב של המפעל הראשון", "סולם יעקב"),
    ("שם הסבא של המייסד", "יעקב"),
]
ANSWERS = ["סולם יעקב", "יעקב"]


def fingerprint(seed: str) -> HardwareFingerprint:
    """A stable, fake machine identity, so tests do not depend on the host."""
    return HardwareFingerprint(traits=[Trait(name="test", value=seed, weight=1.0)])


@pytest.fixture
def gate(tmp_path) -> Gate:
    return Gate(store_path=tmp_path / "operator.sealed", fingerprint=fingerprint("A"))


@pytest.fixture
def enrolled(gate, tmp_path) -> tuple[Gate, Path]:
    key = gate.enrol(
        "dadi", PASSWORD, QUESTIONS, key_target=tmp_path / "usb", brand_id="dadi"
    )
    return gate, key


# --------------------------------------------------------------------------- #
# Answers people actually type
# --------------------------------------------------------------------------- #
class TestNormalisation:
    @pytest.mark.parametrize(
        "written",
        ["סולם יעקב", " סולם יעקב ", "סולם-יעקב", "סולם  יעקב", "סוֹלָם יַעֲקֹב"],
    )
    def test_hebrew_spellings_of_one_answer_all_match(self, written):
        assert normalise_answer(written) == normalise_answer("סולם יעקב")

    @pytest.mark.parametrize("written", ["Beit El", "beit el", "BEIT-EL", " Beit  El "])
    def test_latin_spellings_of_one_answer_all_match(self, written):
        assert normalise_answer(written) == normalise_answer("beit el")

    def test_different_answers_stay_different(self):
        assert normalise_answer("סולם יעקב") != normalise_answer("סולם יוסף")

    def test_geresh_variants_are_the_same_character(self):
        assert normalise_answer("צ׳ק") == normalise_answer("צ'ק")


class TestPasswordRules:
    @pytest.mark.parametrize(
        "password", ["short", "aaaaaaaaaaaaaa", "password12345", " leadingspace123"]
    )
    def test_weak_passwords_are_named_and_refused(self, password):
        assert password_problems(password)

    def test_a_reasonable_password_passes(self):
        assert password_problems(PASSWORD) == []


# --------------------------------------------------------------------------- #
# Enrolment
# --------------------------------------------------------------------------- #
class TestEnrolment:
    def test_enrolment_writes_a_findable_key_file(self, gate, tmp_path):
        key = gate.enrol("dadi", PASSWORD, QUESTIONS, key_target=tmp_path / "usb")
        assert key.name == "profileos.key"
        secret, brand = read_key_file(key)
        assert len(secret) == 32 and brand

    def test_only_one_operator_can_ever_exist(self, enrolled, tmp_path):
        gate, _ = enrolled
        with pytest.raises(AccessDenied) as excinfo:
            gate.enrol("someone", PASSWORD, QUESTIONS, key_target=tmp_path / "usb2")
        assert "already has its operator" in str(excinfo.value)

    def test_fewer_than_two_questions_is_refused(self, gate, tmp_path):
        with pytest.raises(AccessDenied):
            gate.enrol("dadi", PASSWORD, QUESTIONS[:1], key_target=tmp_path / "usb")

    def test_a_weak_password_is_refused_at_enrolment(self, gate, tmp_path):
        with pytest.raises(AccessDenied):
            gate.enrol("dadi", "abc", QUESTIONS, key_target=tmp_path / "usb")

    def test_nothing_secret_is_stored_in_the_clear(self, enrolled):
        """The sealed store must not contain the password or any answer."""
        gate, _ = enrolled
        blob = gate.store_path.read_bytes()
        for secret in [PASSWORD, *ANSWERS]:
            assert secret.encode("utf-8") not in blob
        assert b"dadi" not in blob            # even the username is sealed

    def test_the_key_file_carries_no_credential(self, enrolled):
        """Whoever finds the USB key must not learn the password from it."""
        gate, key = enrolled
        text = key.read_text(encoding="utf-8")
        for secret in [PASSWORD, *ANSWERS]:
            assert secret not in text
        assert set(json.loads(text)) >= {"kind", "secret", "brand_id"}


# --------------------------------------------------------------------------- #
# Getting in, and not getting in
# --------------------------------------------------------------------------- #
class TestAuthentication:
    def test_all_three_factors_together_open_it(self, enrolled):
        gate, key = enrolled
        session = gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)
        assert session.username == "dadi"
        assert session.brand_id == "dadi"

    def test_without_the_key_file_nothing_opens(self, enrolled, monkeypatch):
        gate, _ = enrolled
        monkeypatch.setattr(
            "profileos.security.gate.find_key_files", lambda: []
        )
        with pytest.raises(AccessDenied) as excinfo:
            gate.authenticate("dadi", PASSWORD, ANSWERS)
        assert "key file" in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "username,password,answers",
        [
            ("dadi", "Wrong-Password-Here9", ANSWERS),
            ("dadi", PASSWORD, ["סולם יעקב", "לא נכון"]),
            ("dadi", PASSWORD, ["לא נכון", "יעקב"]),
            ("someone-else", PASSWORD, ANSWERS),
            ("dadi", PASSWORD, ["סולם יעקב"]),          # an answer missing
        ],
    )
    def test_any_wrong_factor_denies(self, enrolled, username, password, answers):
        gate, key = enrolled
        with pytest.raises(AccessDenied):
            gate.authenticate(username, password, answers, key_path=key)

    def test_the_refusal_never_says_which_factor_was_wrong(self, enrolled):
        """Naming the wrong factor turns guessing into three short searches."""
        gate, key = enrolled
        messages = set()
        for username, password, answers in [
            ("dadi", "Wrong-Password-Here9", ANSWERS),
            ("nobody", PASSWORD, ANSWERS),
            ("dadi", PASSWORD, ["x", "y"]),
        ]:
            with pytest.raises(AccessDenied) as excinfo:
                gate.authenticate(username, password, answers, key_path=key)
            messages.add(str(excinfo.value))
        assert len(messages) == 1

    def test_answers_are_accepted_however_they_are_spelled(self, enrolled):
        gate, key = enrolled
        assert gate.authenticate(
            "dadi", PASSWORD, [" סולם-יעקב ", "יַעֲקֹב"], key_path=key
        )

    def test_the_username_is_not_case_sensitive_but_the_password_is(self, enrolled):
        gate, key = enrolled
        assert gate.authenticate("DADI", PASSWORD, ANSWERS, key_path=key)
        with pytest.raises(AccessDenied):
            gate.authenticate("dadi", PASSWORD.upper(), ANSWERS, key_path=key)


class TestMachineBinding:
    def test_a_copied_key_does_not_work_on_another_machine(self, enrolled, tmp_path):
        """The whole point of binding: the stick alone is not enough."""
        gate, key = enrolled
        elsewhere = Gate(store_path=gate.store_path, fingerprint=fingerprint("B"))
        with pytest.raises(AccessDenied) as excinfo:
            elsewhere.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)
        assert "machine" in str(excinfo.value).lower() or "match" in str(excinfo.value)

    def test_a_second_machine_can_be_authorised_from_the_first(self, enrolled):
        gate, key = enrolled
        other = fingerprint("B")
        gate.authorise_machine(
            "dadi", PASSWORD, ANSWERS, fingerprint=other.fingerprint, key_path=key
        )
        elsewhere = Gate(store_path=gate.store_path, fingerprint=other)
        assert elsewhere.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)

    def test_authorising_one_machine_does_not_open_the_door_to_a_third(self, enrolled):
        """Each machine gets its own wrapped copy of the key, and no more."""
        gate, key = enrolled
        gate.authorise_machine(
            "dadi", PASSWORD, ANSWERS,
            fingerprint=fingerprint("B").fingerprint, key_path=key,
        )
        stranger = Gate(store_path=gate.store_path, fingerprint=fingerprint("C"))
        with pytest.raises(AccessDenied):
            stranger.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)

    def test_both_machines_share_one_operator_not_two(self, enrolled):
        """A second machine must not fork the account into two records.

        Change the password from the shop-floor machine and the office machine
        has to be changed too — otherwise the old password would live on
        wherever it was not rotated.
        """
        gate, key = enrolled
        other = fingerprint("B")
        gate.authorise_machine(
            "dadi", PASSWORD, ANSWERS, fingerprint=other.fingerprint, key_path=key
        )
        shop = Gate(store_path=gate.store_path, fingerprint=other)
        replacement = "Beit-El-9063100-Aluminium"
        shop.rotate("dadi", PASSWORD, ANSWERS, new_password=replacement, key_path=key)

        assert gate.authenticate("dadi", replacement, ANSWERS, key_path=key)
        with pytest.raises(AccessDenied):
            gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)

    def test_the_record_is_sealed_once_however_many_machines(self, enrolled):
        """One encrypted record, one wrap per machine — not one record each."""
        gate, key = enrolled
        before = json.loads(gate.store_path.read_bytes()[len(b"PROFILEOS-GATE\x02"):])
        gate.authorise_machine(
            "dadi", PASSWORD, ANSWERS,
            fingerprint=fingerprint("B").fingerprint, key_path=key,
        )
        after = json.loads(gate.store_path.read_bytes()[len(b"PROFILEOS-GATE\x02"):])
        assert len(before["wraps"]) == 1
        assert len(after["wraps"]) == 2
        assert isinstance(after["record"], str)

    def test_a_foreign_key_file_is_refused(self, enrolled, tmp_path):
        gate, _ = enrolled
        foreign = write_key_file(tmp_path / "other", b"\x00" * 32, "someone-else")
        with pytest.raises(AccessDenied):
            gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=foreign)


class TestGuessing:
    def test_repeated_failures_close_the_gate(self, enrolled):
        gate, key = enrolled
        for _ in range(4):
            with pytest.raises(AccessDenied):
                gate.authenticate("dadi", "Wrong-Password-Here9", ANSWERS, key_path=key)
        # The next attempt is refused even with the right password.
        with pytest.raises(AccessDenied) as excinfo:
            gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)
        assert "Too many failed attempts" in str(excinfo.value)

    def test_the_lockout_survives_a_restart(self, enrolled):
        """Counting failures in memory would make restarting the way past it."""
        gate, key = enrolled
        for _ in range(4):
            with pytest.raises(AccessDenied):
                gate.authenticate("dadi", "Wrong-Password-Here9", ANSWERS, key_path=key)
        fresh = Gate(store_path=gate.store_path, fingerprint=gate.fingerprint)
        with pytest.raises(AccessDenied) as excinfo:
            fresh.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)
        assert "Too many failed attempts" in str(excinfo.value)

    def test_a_success_clears_the_count(self, enrolled):
        gate, key = enrolled
        for _ in range(2):
            with pytest.raises(AccessDenied):
                gate.authenticate("dadi", "Wrong-Password-Here9", ANSWERS, key_path=key)
        gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)
        for _ in range(3):
            with pytest.raises(AccessDenied):
                gate.authenticate("dadi", "Wrong-Password-Here9", ANSWERS, key_path=key)
        # Three more failures after a reset is still inside the free window.
        assert gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)


class TestMaintenance:
    def test_the_password_can_be_changed_by_proving_the_old_one(self, enrolled):
        gate, key = enrolled
        gate.rotate(
            "dadi", PASSWORD, ANSWERS,
            new_password="Har-Bracha-2-Shomron", key_path=key,
        )
        assert gate.authenticate("dadi", "Har-Bracha-2-Shomron", ANSWERS, key_path=key)
        with pytest.raises(AccessDenied):
            gate.authenticate("dadi", PASSWORD, ANSWERS, key_path=key)

    def test_the_answers_alone_are_not_a_way_round_the_password(self, enrolled):
        """Security questions are a second factor, never a reset route."""
        gate, key = enrolled
        with pytest.raises(AccessDenied):
            gate.rotate(
                "dadi", "Wrong-Password-Here9", ANSWERS,
                new_password="Har-Bracha-2-Shomron", key_path=key,
            )

    def test_the_questions_can_be_replaced(self, enrolled):
        gate, key = enrolled
        gate.rotate(
            "dadi", PASSWORD, ANSWERS,
            new_password=PASSWORD,
            new_questions=[("שאלה חדשה", "תשובה"), ("עוד שאלה", "עוד תשובה")],
            key_path=key,
        )
        assert gate.prompts(key_path=key) == ["שאלה חדשה", "עוד שאלה"]
        assert gate.authenticate(
            "dadi", PASSWORD, ["תשובה", "עוד תשובה"], key_path=key
        )

    def test_the_prompts_need_the_key_to_read(self, enrolled, monkeypatch):
        """The questions hint at their answers; do not show them to a stranger."""
        gate, key = enrolled
        assert gate.prompts(key_path=key)
        monkeypatch.setattr("profileos.security.gate.find_key_files", lambda: [])
        with pytest.raises(AccessDenied):
            gate.prompts()

    def test_an_uninstalled_gate_says_so(self, gate):
        with pytest.raises(NotEnrolled):
            gate._load(b"\x00" * 32)
        assert gate.status()["enrolled"] is False
