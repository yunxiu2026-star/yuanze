from __future__ import annotations

import re
import os
from urllib.parse import urlsplit, urlunsplit

from .models import RecordType

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
COUNTRY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,79}$")
UNSAFE_MARKUP_RE = re.compile(r"<\s*/?\s*(script|style|iframe|object|embed|svg|html)|javascript:", re.I)
ANY_TAG_RE = re.compile(r"<[^>]+>")
ALLOWED_CONTEXT_KEYS = {
    "source_page", "source_route", "source_product", "source_service", "source_market",
    "source_guide", "source_brief_id", "case_context", "manufacturing_context",
    "quality_context", "series", "product_type", "product_direction", "formula_concept",
    "texture_direction", "packaging_direction", "starting_point", "brief", "utm",
}
ALLOWED_UTM_KEYS = {"source", "medium", "campaign", "term", "content"}
MAX_TEXT = 4000
PRODUCT_BRIEF_FIELDS = {
    "name", "company", "work_email", "country", "whatsapp", "series", "product_type",
    "starting_point", "product_direction", "formula_concept", "texture_direction",
    "packaging_direction", "target_market", "expected_quantity", "launch_timeline",
    "project_context", "consent_inquiry", "consent_upload",
}
PRODUCT_BRIEF_SERIES = set(os.getenv("PRODUCT_BRIEF_SERIES", "face,head,body,unsure").split(","))
PRODUCT_BRIEF_STARTING_POINTS = {"idea", "formula-direction", "reference-sample", "own-specification"}
QUANTITY_RANGE_RE = re.compile(r"^[0-9][0-9, ]*(?:[-–][0-9][0-9, ]*)?$")
PHONE_RE = re.compile(r"^[+0-9() .-]{0,40}$")


class ValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]):
        super().__init__("Submission validation failed")
        self.field_errors = field_errors


def _text(value: object, field: str, errors: dict[str, str], *, required: bool = False, maximum: int = MAX_TEXT) -> str:
    if value is not None and not isinstance(value, str):
        errors[field] = "text_required"
        return ""
    normalized = " ".join(str(value or "").split())
    if required and not normalized:
        errors[field] = "required"
    elif len(normalized) > maximum:
        errors[field] = "too_long"
    elif UNSAFE_MARKUP_RE.search(normalized) or ANY_TAG_RE.search(normalized):
        errors[field] = "unsafe_markup"
    return normalized


def _current_form_fields(record_type, fields, errors):
    """Accept the current complete forms alongside the compact sample popup."""
    common = {"company": (True, 160), "country": (True, 80), "work_email": (True, 254), "whatsapp": (False, 40), "expected_quantity": (True, 40), "target_market": (True, 120), "reference_product": (False, 240)}
    if record_type is RecordType.SAMPLE_REQUEST:
        specs = {**common, "name": (True, 120), "product": (True, 200), "formula_preference": (True, 40), "packaging_preference": (True, 240), "launch_timeline": (True, 120), "message": (False, 4000)}
        formula_key = "formula_preference"
    else:
        specs = {**common, "contact_name": (True, 120), "website_social": (False, 2048), "product_category": (True, 40), "product_idea": (True, 1200), "formula_route": (True, 40), "target_price_range": (False, 120), "packaging_requirement": (True, 1200), "launch_date": (True, 120), "certification_testing_requirement": (False, 1200), "additional_notes": (False, 4000)}
        formula_key = "formula_route"
    if set(fields) - set(specs) - {"consent_inquiry", "consent_upload"}:
        errors["submitted_fields"] = "unsupported_field"
    result = {key: _text(fields.get(key), key, errors, required=required, maximum=maximum) for key, (required, maximum) in specs.items()}
    result["work_email"] = result["work_email"].lower()
    if result["work_email"] and not EMAIL_RE.fullmatch(result["work_email"]):
        errors["work_email"] = "invalid_email"
    if result["country"] and not COUNTRY_RE.fullmatch(result["country"]):
        errors["country"] = "invalid_country"
    if result["whatsapp"] and not PHONE_RE.fullmatch(result["whatsapp"]):
        errors["whatsapp"] = "invalid_phone"
    if result[formula_key] and result[formula_key] not in {"Stock Formula", "Semi-custom", "Custom Formula", "Unsure"}:
        errors[formula_key] = "unsupported_value"
    if record_type is RecordType.RFQ and result["product_category"] not in set(os.getenv('RFQ_CATEGORIES','Skincare,Hair care,Body care,Other / unsure').split(',')):
        errors["product_category"] = "unsupported_value"
    quantity = result["expected_quantity"]
    if quantity:
        if not QUANTITY_RANGE_RE.fullmatch(quantity):
            errors["expected_quantity"] = "invalid_quantity"
        else:
            values = [int(item.replace(",", "").replace(" ", "")) for item in re.split(r"[-–]", quantity)]
            if any(value <= 0 or value > 1_000_000_000 for value in values) or (len(values) == 2 and values[1] < values[0]):
                errors["expected_quantity"] = "invalid_quantity"
    if record_type is RecordType.RFQ and result["website_social"]:
        result["website_social"] = _url(result["website_social"], "website_social", errors)
    if fields.get("consent_inquiry") is not True:
        errors["consent_inquiry"] = "explicit_consent_required"
    result["consent_inquiry"] = fields.get("consent_inquiry") is True
    result["consent_upload"] = fields.get("consent_upload") is True
    return result


def _url(value: object, field: str, errors: dict[str, str]) -> str:
    raw = _text(value, field, errors, maximum=2048)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        errors[field] = "invalid_url"
        return raw
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        errors[field] = "invalid_url"
        return raw
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "", parts.query, ""))


def normalize_source_context(value: object, errors: dict[str, str]) -> dict[str, object]:
    if value is None:
        return {"trust": "CLIENT_PROVIDED_CONTEXT"}
    if not isinstance(value, dict) or set(value) - ALLOWED_CONTEXT_KEYS:
        errors["source_context"] = "invalid_shape"
        return {"trust": "CLIENT_PROVIDED_CONTEXT"}
    result: dict[str, object] = {"trust": "CLIENT_PROVIDED_CONTEXT"}
    for key, item in value.items():
        if key == "utm":
            if not isinstance(item, dict) or set(item) - ALLOWED_UTM_KEYS:
                errors["source_context.utm"] = "invalid_shape"
            else:
                result[key] = {k: _text(v, f"utm.{k}", errors, maximum=200) for k, v in item.items()}
        else:
            result[key] = _text(item, f"source_context.{key}", errors, maximum=200)
    return result


def _product_brief_fields(fields: dict[str, object], errors: dict[str, str]) -> dict[str, object]:
    if set(fields) - PRODUCT_BRIEF_FIELDS:
        errors["submitted_fields"] = "unsupported_field"
    normalized: dict[str, object] = {
        "name": _text(fields.get("name"), "name", errors, required=True, maximum=120),
        "company": _text(fields.get("company"), "company", errors, required=True, maximum=160),
        "work_email": _text(fields.get("work_email"), "work_email", errors, required=True, maximum=254).lower(),
        "country": _text(fields.get("country"), "country", errors, required=True, maximum=80),
        "whatsapp": _text(fields.get("whatsapp"), "whatsapp", errors, maximum=40),
        "series": _text(fields.get("series"), "series", errors, required=True, maximum=20).lower(),
        "product_type": _text(fields.get("product_type"), "product_type", errors, required=True, maximum=120),
        "starting_point": _text(fields.get("starting_point"), "starting_point", errors, required=True, maximum=40).lower(),
        "product_direction": _text(fields.get("product_direction"), "product_direction", errors, required=True, maximum=500),
        "formula_concept": _text(fields.get("formula_concept"), "formula_concept", errors, maximum=240),
        "texture_direction": _text(fields.get("texture_direction"), "texture_direction", errors, maximum=240),
        "packaging_direction": _text(fields.get("packaging_direction"), "packaging_direction", errors, maximum=240),
        "target_market": _text(fields.get("target_market"), "target_market", errors, required=True, maximum=120),
        "expected_quantity": _text(fields.get("expected_quantity"), "expected_quantity", errors, maximum=40),
        "launch_timeline": _text(fields.get("launch_timeline"), "launch_timeline", errors, maximum=120),
        "project_context": _text(fields.get("project_context"), "project_context", errors, maximum=800),
        "consent_inquiry": fields.get("consent_inquiry") is True,
        "consent_upload": fields.get("consent_upload") is True,
    }
    if normalized["work_email"] and not EMAIL_RE.fullmatch(str(normalized["work_email"])):
        errors["work_email"] = "invalid_email"
    if normalized["country"] and not COUNTRY_RE.fullmatch(str(normalized["country"])):
        errors["country"] = "invalid_country"
    if normalized["whatsapp"] and not PHONE_RE.fullmatch(str(normalized["whatsapp"])):
        errors["whatsapp"] = "invalid_phone"
    if normalized["series"] and normalized["series"] not in PRODUCT_BRIEF_SERIES:
        errors["series"] = "unsupported_value"
    if normalized["starting_point"] and normalized["starting_point"] not in PRODUCT_BRIEF_STARTING_POINTS:
        errors["starting_point"] = "unsupported_value"
    quantity = str(normalized["expected_quantity"])
    if quantity:
        if not QUANTITY_RANGE_RE.fullmatch(quantity):
            errors["expected_quantity"] = "invalid_quantity"
        else:
            values = [int(item.replace(",", "").replace(" ", "")) for item in re.split(r"[-–]", quantity)]
            if any(value <= 0 or value > 1_000_000_000 for value in values) or (len(values) == 2 and values[1] < values[0]):
                errors["expected_quantity"] = "invalid_quantity"
    if fields.get("consent_inquiry") is not True:
        errors["consent_inquiry"] = "explicit_consent_required"
    return normalized


def validate_submission(record_type: RecordType, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValidationError({"body": "object_required"})
    errors: dict[str, str] = {}
    fields = payload.get("submitted_fields")
    if not isinstance(fields, dict):
        raise ValidationError({"submitted_fields": "object_required"})
    if record_type in {RecordType.SAMPLE_REQUEST, RecordType.RFQ} and "work_email" in fields:
        normalized = _current_form_fields(record_type, fields, errors)
        source_route = _text(payload.get("source_route"), "source_route", errors, required=True, maximum=300)
        if not source_route.startswith("/") or "://" in source_route:
            errors["source_route"] = "relative_route_required"
        context = normalize_source_context(payload.get("source_context"), errors)
        if errors:
            raise ValidationError(errors)
        return {"record_type": record_type, "source_route": source_route, "source_context": context, "submitted_fields": normalized}
    if record_type is RecordType.PRODUCT_BRIEF:
        normalized = _product_brief_fields(fields, errors)
        source_route = _text(payload.get("source_route"), "source_route", errors, required=True, maximum=300)
        if not source_route.startswith("/") or "://" in source_route:
            errors["source_route"] = "relative_route_required"
        context = normalize_source_context(payload.get("source_context"), errors)
        if errors:
            raise ValidationError(errors)
        return {"record_type": record_type, "source_route": source_route, "source_context": context, "submitted_fields": normalized}

    allowed = {"name", "email", "country", "quantity", "website", "message", "product", "consent"}
    if set(fields) - allowed:
        errors["submitted_fields"] = "unsupported_field"
    normalized: dict[str, object] = {
        "name": _text(fields.get("name"), "name", errors, required=True, maximum=120),
        "email": _text(fields.get("email"), "email", errors, required=True, maximum=254).lower(),
        "country": _text(fields.get("country"), "country", errors, required=True, maximum=80),
        "message": _text(fields.get("message"), "message", errors, required=True),
    }
    if normalized["email"] and not EMAIL_RE.fullmatch(str(normalized["email"])):
        errors["email"] = "invalid_email"
    if normalized["country"] and not COUNTRY_RE.fullmatch(str(normalized["country"])):
        errors["country"] = "invalid_country"
    quantity = fields.get("quantity")
    if quantity not in (None, ""):
        try:
            quantity = int(str(quantity).replace(",", ""))
            if quantity <= 0 or quantity > 1_000_000_000:
                raise ValueError
            normalized["quantity"] = quantity
        except (TypeError, ValueError):
            errors["quantity"] = "invalid_quantity"
    if "website" in fields:
        normalized["website"] = _url(fields.get("website"), "website", errors)
    if "product" in fields:
        normalized["product"] = _text(fields.get("product"), "product", errors, maximum=200)
    if fields.get("consent") is not True:
        errors["consent"] = "explicit_consent_required"
    normalized["consent"] = fields.get("consent") is True
    source_route = _text(payload.get("source_route"), "source_route", errors, required=True, maximum=300)
    if not source_route.startswith("/") or "://" in source_route:
        errors["source_route"] = "relative_route_required"
    context = normalize_source_context(payload.get("source_context"), errors)
    if errors:
        raise ValidationError(errors)
    return {"record_type": record_type, "source_route": source_route, "source_context": context, "submitted_fields": normalized}


# Ads closure: accept only these bounded, non-authoritative attribution additions.
# All legacy fields, unknown-key rejection and trust handling keep the original validator.
_ads_legacy_normalize_source_context = normalize_source_context

def normalize_source_context(value: object, errors: dict[str, str]):
    import re
    if not isinstance(value, dict):
        return _ads_legacy_normalize_source_context(value, errors)
    original = dict(value)
    additions = {}
    limits = {'landing_page': 500, 'first_landing_page': 500, 'gclid': 200, 'ad_source_tag': 1000}
    for key, maximum in limits.items():
        if key not in original:
            continue
        item = original.pop(key)
        if item in ('', None):
            continue
        valid = isinstance(item, str) and len(item) <= maximum and not re.search(r'[\x00-\x1f\x7f]', item)
        if valid and key in {'landing_page', 'first_landing_page'}:
            valid = bool(re.fullmatch(r'/(?!/)[a-zA-Z0-9/_~.%-]*', item)) and not any(p in {'.', '..'} for p in item.split('/'))
        elif valid and key == 'gclid':
            valid = bool(re.fullmatch(r'[A-Za-z0-9._~-]{1,200}', item))
        elif valid and key == 'ad_source_tag':
            valid = bool(re.fullmatch(r'\[Ad Source: [^\[\]\r\n]+\]', item))
        if not valid:
            errors['source_context'] = 'invalid_shape'
        else:
            additions[key] = item
    result = _ads_legacy_normalize_source_context(original, errors)
    result.update(additions)
    return result
