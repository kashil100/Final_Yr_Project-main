import re
from dataclasses import dataclass
from difflib import SequenceMatcher


AADHAAR_DIGITS_RE = re.compile(r"^\d{12}$")
MIN_NAME_MATCH_SCORE = 0.75


@dataclass(frozen=True)
class AadhaarVerificationResult:
    is_valid: bool
    verification_status: str
    error_message: str = ""
    matched_name: str = ""
    match_score: float = 0.0


class AadhaarVerificationService:
    @staticmethod
    def normalize_name(value):
        normalized = re.sub(r"[^a-z0-9\s]", " ", (value or "").strip().lower())
        return " ".join(normalized.split())

    @classmethod
    def validate_aadhaar_number(cls, aadhaar_number):
        return bool(AADHAAR_DIGITS_RE.fullmatch((aadhaar_number or "").strip()))

    @classmethod
    def names_match(cls, full_name, aadhaar_holder_name):
        normalized_full_name = cls.normalize_name(full_name)
        normalized_holder_name = cls.normalize_name(aadhaar_holder_name)
        if not normalized_full_name or not normalized_holder_name:
            return False, 0.0

        similarity = SequenceMatcher(
            None,
            normalized_full_name,
            normalized_holder_name,
        ).ratio()
        full_name_tokens = set(normalized_full_name.split())
        holder_name_tokens = set(normalized_holder_name.split())
        token_overlap = (
            len(full_name_tokens & holder_name_tokens) / max(len(full_name_tokens), 1)
        )
        score = max(similarity, token_overlap)
        return score >= MIN_NAME_MATCH_SCORE, score

    @classmethod
    def verify_registration_details(
        cls,
        full_name,
        aadhaar_number,
        aadhaar_holder_name,
    ):
        if not cls.validate_aadhaar_number(aadhaar_number):
            return AadhaarVerificationResult(
                is_valid=False,
                verification_status="rejected",
                error_message="Aadhaar number must contain exactly 12 digits.",
            )

        names_match, score = cls.names_match(full_name, aadhaar_holder_name)
        if not names_match:
            return AadhaarVerificationResult(
                is_valid=False,
                verification_status="rejected",
                error_message="Volunteer name does not sufficiently match the Aadhaar holder name.",
                match_score=score,
            )

        return AadhaarVerificationResult(
            is_valid=True,
            verification_status="verified",
            matched_name=cls.normalize_name(aadhaar_holder_name),
            match_score=score,
        )
