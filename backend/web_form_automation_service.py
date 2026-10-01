"""Web Form automation. Fail-closed: any missing field, unrecognized
structure, or human-verification/login signal stops automation."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("inquiry.web_form_automation")

# Mechanisms meaning "a human must complete this" — triggers the escalation path.
HUMAN_VERIFICATION_MECHANISMS = (
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "cloudflare_challenge",
    "unknown_human_verification",
)

_RECAPTCHA_SIGNATURES = ("recaptcha", "i'm not a robot", "im not a robot")
_HCAPTCHA_SIGNATURES = ("hcaptcha",)
_TURNSTILE_SIGNATURES = ("turnstile", "cf-turnstile")
_CLOUDFLARE_SIGNATURES = (
    "checking your browser",
    "cloudflare",
    "ray id",
    "just a moment",
)
_GENERIC_HUMAN_VERIFICATION_SIGNATURES = (
    "verify you are human",
    "human verification",
    "security challenge",
)
_LOGIN_SIGNATURES = ("sign in", "log in", "login", "register", "create an account")

# Both mean "a human needs to look at this"; kept distinct since one means
# nothing was submitted yet and the other means it may already have been.
ESCALATION_OUTCOMES = ("human_action_required", "submitted_but_unverified")

# Env-driven, not hardcoded: PREPARE fallback (fill-only, never submits)
# can default on.
def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


GENERIC_ENGINE_ENABLED = _env_flag("WEBFORM_GENERIC_ENGINE_ENABLED", default=True)

# Authorization is DB-derived (target_authorized, below), not a static
# hostname list — every other safety gate still applies independently.

# CAPTCHA widgets are often injected asynchronously after `load` fires
# (confirmed: checking immediately missed reCAPTCHA on 3/5 real hosts).
GATE_SETTLE_NETWORK_IDLE_TIMEOUT_MS = 5000
GATE_SETTLE_EXTRA_DELAY_MS = 2000


async def _wait_for_gate_settle(page) -> None:
    """Settle before detection; times out safely rather than hanging."""
    try:
        await page.wait_for_load_state("networkidle", timeout=GATE_SETTLE_NETWORK_IDLE_TIMEOUT_MS)
    except Exception:
        pass
    await page.wait_for_timeout(GATE_SETTLE_EXTRA_DELAY_MS)


# Best-effort, generic — cookie banners (OneTrust, confirmed on Merck/
# Sanofi) would otherwise intercept clicks meant for the real form.
async def _dismiss_cookie_banner(page) -> None:
    for selector in ("#onetrust-accept-btn-handler",):
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


@dataclass(frozen=True)
class FieldMapping:
    selector: str
    # Exactly one of source_key/constant_value; constant_value wins if both set.
    source_key: Optional[str] = None
    constant_value: Optional[str] = None
    required: bool = False
    field_type: str = "text"  # "text" | "checkbox" | "select" | "combobox" | "dual_listbox"
    label: Optional[str] = None  # for error messages; falls back to the dict key
    # dual_listbox only: the "Add"/move-right control clicked after selecting
    # the option in `selector` (a left-hand <select multiple>).
    companion_selector: Optional[str] = None
    # Explicit adapter opt-in: fall back to a literal "Other" option on no
    # exact match. Never automatic — some sites' "Other" means something else.
    fallback_to_other_option: bool = False


def split_requester_name(full_name: Optional[str]) -> tuple:
    """Splits into (first, last): "" -> ("",""); "Madonna" -> ("Madonna","");
    "Leah Kim" -> ("Leah","Kim"); "Mary Jane Watson" -> ("Mary","Jane Watson")."""
    if not full_name:
        return "", ""
    parts = full_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _resolve_field_value(mapping: FieldMapping, inquiry_data: dict) -> str:
    """The one place that resolves a field's fill value; constant_value wins."""
    if mapping.constant_value is not None:
        return mapping.constant_value.strip()
    if mapping.source_key:
        return (inquiry_data.get(mapping.source_key) or "").strip()
    return ""


@dataclass(frozen=True)
class WebFormAdapter:
    key: str
    label: str
    # Substring matched against the target URL's hostname — None only for
    # the mock adapter, which is never resolved by URL.
    url_match: Optional[str]
    automation_enabled: bool
    allow_real_submission: bool
    field_mappings: dict = field(default_factory=dict)  # str -> FieldMapping
    submit_selector: Optional[str] = None
    # Verification order: error_selector -> success_url_contains ->
    # confirmation_selector -> ambiguous (submitted_but_unverified) -> failed.
    confirmation_selector: Optional[str] = None
    error_selector: Optional[str] = None
    success_url_contains: Optional[str] = None
    confirmation_timeout_ms: int = 3000
    # One explicitly adapter-declared pre-form click (e.g. an HCP attestation
    # button) — never a generic "click anything" mechanism.
    pre_form_selector: Optional[str] = None
    disabled_reason: Optional[str] = None  # only for disabled real adapters
    disabled_mechanism: Optional[str] = None  # one of HUMAN_VERIFICATION_MECHANISMS, or None
    # Only set True once an author verifies this URL is actually unique per
    # submission — most "Thank You" pages are generic/static.
    confirmation_url_is_unique: bool = False


@dataclass
class WebFormAutomationResult:
    outcome: str  # "automation_success" | "automation_failed" | "human_action_required" | "submitted_but_unverified"
    reason: str
    target: str  # "manufacturer" | "mock_test"
    stage: str  # "prepare" | "submit"
    mechanism: Optional[str] = None
    filled_fields: list = field(default_factory=list)
    missing_fields: list = field(default_factory=list)
    # Evidence for the Inquiry UI only — never affects automation_success
    # itself. Set only when the URL match is adapter-verified unique.
    confirmation_url: Optional[str] = None
    # Raw PNG bytes of the confirmation screen, captured when no unique
    # confirmation_url exists. Router uploads to S3.
    confirmation_screenshot_bytes: Optional[bytes] = None


# Local mock/test adapter — the only one ever automation_enabled.
_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "web_forms")


def mock_fixture_url(name: str = "mock_form_ok.html") -> str:
    return "file://" + os.path.join(_FIXTURES_DIR, name)


MOCK_ADAPTER = WebFormAdapter(
    key="mock_test_form",
    label="Local mock test form (POC only — never a real manufacturer)",
    url_match=None,
    automation_enabled=True,
    allow_real_submission=True,
    field_mappings={
        "drug_name": FieldMapping(selector="#drug_name", source_key="medication_name"),
        "question": FieldMapping(selector="#question", source_key="question", required=True),
        "requester_name": FieldMapping(selector="#requester_name", source_key="requester_name"),
        "requester_email": FieldMapping(selector="#requester_email", source_key="requester_email", required=True),
        "team_name": FieldMapping(selector="#team_name", source_key="team_name"),
        "subject": FieldMapping(selector="#subject", source_key="subject"),
    },
    submit_selector="#submit-btn",
    confirmation_selector="#confirmation",
    # Fixture never navigates (preventDefault()) — keep the wait short so
    # tests aren't stuck waiting out the full navigation timeout for nothing.
    confirmation_timeout_ms=800,
)


# Resolving an adapter never makes a network call; automation_enabled=True
# also needs target_authorized=True (mi_web_form_url on file).
REAL_ADAPTERS: list = [
    WebFormAdapter(
        key="pmiform",
        label="pmiform.com (Seagen / Pharmacia & Upjohn / Hospira)",
        url_match="pmiform.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason="Verified via curl/WebFetch: Cloudflare bot-block page returned before any form is reachable.",
        disabled_mechanism="cloudflare_challenge",
    ),
    WebFormAdapter(
        key="astrazeneca",
        label="AstraZeneca (contactazmedical.astrazeneca.com)",
        url_match="astrazeneca.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason="Verified via curl/WebFetch: 403, Cloudflare-blocked.",
        disabled_mechanism="cloudflare_challenge",
    ),
    WebFormAdapter(
        key="abbvie",
        label="AbbVie (abbviemedinfo.com)",
        url_match="abbviemedinfo.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason="Verified: page unconditionally initializes Google reCAPTCHA Enterprise on load.",
        disabled_mechanism="recaptcha",
    ),
    WebFormAdapter(
        key="jnj",
        label="Johnson & Johnson (jnjmedicalconnect.com)",
        url_match="jnjmedicalconnect.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason="Verified: page's own client config states reCAPTCHA is required.",
        disabled_mechanism="recaptcha",
    ),
    WebFormAdapter(
        key="daiichi_sankyo",
        label="Daiichi Sankyo (dsimedinfo.com)",
        url_match="dsimedinfo.com",
        automation_enabled=False,
        allow_real_submission=False,
        # Verified live against the Liferay "medicalinquirycontactcenter" portlet.
        field_mappings={
            "product": FieldMapping(
                selector="#_medicalinquirycontactcenter_product",
                source_key="medication_name",
                required=True,
                field_type="select",
                label="Product",
            ),
            # "Email" is the only reply channel our workflow can actually receive.
            "preferred_response": FieldMapping(
                selector="#_medicalinquirycontactcenter_preferredResponse",
                constant_value="Email",
                required=True,
                field_type="select",
                label="Preferred Response",
            ),
            # No Salutation/Credentials/Phone on Inquiry — left as source_key
            # lookups so a missing value fails closed, never guessed.
            "salutation": FieldMapping(
                selector="#_medicalinquirycontactcenter_salutation",
                source_key="requester_salutation",
                required=True,
                field_type="select",
                label="Salutation",
            ),
            "first_name": FieldMapping(
                selector="#_medicalinquirycontactcenter_firstName",
                source_key="requester_first_name",
                required=True,
                label="First Name",
            ),
            "last_name": FieldMapping(
                selector="#_medicalinquirycontactcenter_lastName",
                source_key="requester_last_name",
                required=True,
                label="Last Name",
            ),
            "credentials": FieldMapping(
                selector="#_medicalinquirycontactcenter_credentials",
                source_key="requester_credentials",
                required=True,
                field_type="select",
                label="Credentials",
            ),
            "phone": FieldMapping(
                selector="#_medicalinquirycontactcenter_phone",
                source_key="requester_phone",
                required=True,
                label="Phone",
            ),
            "email": FieldMapping(
                selector="#_medicalinquirycontactcenter_email",
                source_key="requester_email",
                required=True,
                label="Email Address",
            ),
            "inquiry": FieldMapping(
                selector="#_medicalinquirycontactcenter_inquiry",
                source_key="question",
                required=True,
                label="Inquiry",
            ),
            # Always true: every dispatch here originates from our own U.S. HCP intake.
            "hcp_attestation": FieldMapping(
                selector="#_medicalinquirycontactcenter_accept",
                constant_value="true",
                required=True,
                field_type="checkbox",
                label="U.S. Healthcare Professional Attestation",
            ),
        },
        # Not configured: real submit is same-page AJAX with no navigation, and
        # success/failure render into the same element, distinguishable only by text.
        submit_selector=None,
        confirmation_selector=None,
        error_selector=None,
        success_url_contains=None,
        pre_form_selector="#healthCareProfessional",  # real HCP-attestation modal button
        disabled_reason=(
            "Verified live (browser inspection, 2026-09-28): an invisible "
            "reCAPTCHA v3 widget (grecaptcha.execute, sitekey "
            "6LfSw6kaAAAAAEs9LogdqFRo8eZByvqlQvODNkaC) is present in the DOM "
            "from page load and executes automatically at submit time; its "
            "token is written into a hidden field "
            "(#_medicalinquirycontactcenter_token) and posted as part of an "
            "in-page AJAX request (no page navigation occurs at all). "
            "Additionally, Phone/Salutation/Credentials have no corresponding "
            "Inquiry data source today, and success/failure are rendered "
            "into the same DOM element distinguished only by text, which the "
            "generic verification model cannot safely tell apart. "
            "automation_enabled stays False until these are resolved."
        ),
        disabled_mechanism="recaptcha",
    ),
    WebFormAdapter(
        key="sanofi",
        label="Sanofi (sanofimedicalinformation.com)",
        url_match="sanofimedicalinformation.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason=(
            "Verified live (browser inspection, 2026-09-29): the initial page "
            "load and filled form have no CAPTCHA, but clicking Submit opens "
            "a reCAPTCHA 'I'm not a robot' checkbox modal before the real "
            "submission fires. No data was transmitted — the click was "
            "aborted at the recaptcha gate, confirmed via screenshot and "
            "unchanged form state. All other required fields (Product via "
            "the SLDS combobox, Question, First/Last Name, Email, HCP Type) "
            "are otherwise fillable with deterministic mappings."
        ),
        disabled_mechanism="recaptcha",
    ),
    WebFormAdapter(
        key="lilly",
        label="Eli Lilly (medical.lilly.com)",
        url_match="lilly.com",
        automation_enabled=False,
        allow_real_submission=False,
        disabled_reason="force.com signature suggests the same Salesforce platform as Sanofi — never confirmed in a live browser session; not claimed safe.",
        disabled_mechanism=None,
    ),
    WebFormAdapter(
        key="genentech",
        label="Genentech (roche-ssp.my.salesforce-sites.com — reached via gene.com's embedded form)",
        url_match="roche-ssp.my.salesforce-sites.com",
        automation_enabled=True,
        allow_real_submission=True,
        # Verified live (2026-09-29): no CAPTCHA/login, stable ids. HCP radio
        # is a pre-form click (AJAX reveals more fields); always "Yes" for us.
        pre_form_selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:j_id62\\:0",
        field_mappings={
            # Falls back to "Other" for a non-catalog medication — the real
            # name is still restated in the Question text below.
            "product": FieldMapping(
                selector="#grouped-select",
                source_key="medication_name",
                required=True,
                field_type="select",
                label="Select a product",
                fallback_to_other_option=True,
            ),
            "question": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerQuestion",
                source_key="question",
                required=True,
                label="Type your question",
            ),
            # PHARMD is honest, non-guessed (see WEB_FORM_CONTACT_CREDENTIALS).
            "credentials": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerCredentials\\:j_id71\\:multiselectPanel\\:leftList",
                companion_selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerCredentials\\:j_id71\\:btnRight",
                source_key="requester_credentials",
                required=True,
                field_type="dual_listbox",
                label="Credentials",
            ),
            # This channel is for general MI questions, never adverse-event
            # reports — "No" is always the truthful answer here.
            "adverse_event": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:j_id111\\:1",
                constant_value="true",
                required=True,
                field_type="checkbox",
                label="Adverse event question",
            ),
            # "Email" is the only reply channel our workflow can actually receive.
            "contact_method": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerChannel",
                constant_value="Email",
                required=True,
                field_type="select",
                label="Contact Method",
            ),
            "first_name": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerFirstName",
                source_key="requester_first_name",
                required=True,
                label="First Name",
            ),
            "last_name": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerLastName",
                source_key="requester_last_name",
                required=True,
                label="Last Name",
            ),
            "state": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerState",
                source_key="requester_state",
                required=True,
                field_type="select",
                label="State",
            ),
            "email": FieldMapping(
                selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:customerEmail",
                source_key="requester_email",
                required=True,
                label="Email",
            ),
        },
        submit_selector="#SYN_Portal_Form_USMA_Page\\:SYN_Portal_Form_USMA\\:pageContainer\\:j_id207",
        success_url_contains="SYN_Portal_Form_Thanks",
        confirmation_timeout_ms=5000,
        # Verified: URL is generic/static, no case id — screenshot evidence
        # is used instead of a misleading "verify" link.
        confirmation_url_is_unique=False,
    ),
    WebFormAdapter(
        key="focus_health_group",
        label="Focus Health Group (focushealthgroup.com)",
        url_match="focushealthgroup.com",
        automation_enabled=True,
        allow_real_submission=True,
        # Verified live: plain Gravity Forms, no CAPTCHA. Phone now resolved
        # via WEB_FORM_CONTACT_PHONE.
        field_mappings={
            "first_name": FieldMapping(
                selector="#input_1_1_3", source_key="requester_first_name", required=True, label="First Name",
            ),
            "last_name": FieldMapping(
                selector="#input_1_1_6", source_key="requester_last_name", required=True, label="Last Name",
            ),
            "phone": FieldMapping(
                selector="#input_1_2", source_key="requester_phone", required=True, label="Phone",
            ),
            "email": FieldMapping(
                selector="#input_1_3", source_key="requester_email", required=True, label="Email",
            ),
            "message": FieldMapping(
                selector="#input_1_4", source_key="question", required=True, label="Message",
            ),
        },
        submit_selector="#gform_submit_button_1",
        confirmation_selector="#gform_confirmation_wrapper_1",
        confirmation_timeout_ms=5000,
    ),
    WebFormAdapter(
        key="merck",
        label="Merck / MSD (merckmedicalportal.com)",
        url_match="merckmedicalportal.com",
        automation_enabled=True,
        allow_real_submission=True,
        # Verified live: no CAPTCHA/login; ids drift so selectors use `name`.
        # HCP radio is a pre-form click, always "Yes" for our own intake.
        pre_form_selector="button:has-text('I am a U.S. Health Care Professional')",
        field_mappings={
            "first_name": FieldMapping(
                selector="input[name='firstName']", source_key="requester_first_name",
                required=True, label="First Name",
            ),
            "last_name": FieldMapping(
                selector="input[name='lastName']", source_key="requester_last_name",
                required=True, label="Last Name",
            ),
            "designation": FieldMapping(
                selector="input[name='mir-designation']", constant_value="PharmD",
                field_type="combobox", label="Designation",
            ),
            "organization": FieldMapping(
                selector="input[name='organization']", source_key="team_name",
                required=True, label="Organization/Facility Name",
            ),
            # Content-based, not id-based — id/value drift like everything
            # else here; "Email" is the only channel we can receive.
            "contact_preference": FieldMapping(
                selector="input[name='default']:has(+ label:has-text('Email'))",
                constant_value="true", field_type="checkbox", required=True,
                label="Contact Preference",
            ),
            "email": FieldMapping(
                selector="input[name='EmailId']", source_key="requester_email", label="Email Address",
            ),
            "phone": FieldMapping(
                selector="input[name='phone']", source_key="requester_phone", label="Phone Number",
            ),
            # This picklist uses full state names, not abbreviations — same
            # GA address, different format.
            "state": FieldMapping(
                selector="input[name='cmme-state']", constant_value="Georgia",
                field_type="combobox", required=True, label="State",
            ),
            "zip": FieldMapping(
                selector="input[name='zipValue']", source_key="requester_zip",
                required=True, label="Zip Code",
            ),
            # "Other Medical or Scientific Topic" is the site's own honest
            # catch-all — not a guess about the real reason for contact.
            "reason_for_contact": FieldMapping(
                selector="input[name='reason-contact']", constant_value="Other Medical or Scientific Topic",
                field_type="combobox", required=True, label="Reason for Contact",
            ),
            # No drug-to-therapeutic-area data source exists — "Vaccines" is
            # a documented policy default, not a guess.
            "area_of_interest": FieldMapping(
                selector="input[name='Area-name']", constant_value="Vaccines",
                field_type="combobox", required=True, label="Area of Interest",
            ),
            "question": FieldMapping(
                selector="textarea[name='formItem7']", source_key="question",
                required=True, label="Enter your Question or Request",
            ),
        },
        submit_selector="button:text-is('Submit')",
        confirmation_selector="text='Your request was submitted successfully.'",
        confirmation_timeout_ms=5000,
    ),
]


def resolve_real_adapter(url: Optional[str]) -> Optional[WebFormAdapter]:
    """Matches hostname substring against REAL_ADAPTERS; None if unsupported."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    for adapter in REAL_ADAPTERS:
        if adapter.url_match and adapter.url_match in host:
            return adapter
    return None


def resolve_target(*, mi_web_form_url: Optional[str], use_mock: bool):
    """use_mock=True always wins — the only path that can reach a real fill/submit."""
    if use_mock:
        return mock_fixture_url(), MOCK_ADAPTER, "mock_test"
    return mi_web_form_url, resolve_real_adapter(mi_web_form_url), "manufacturer"


# Detection only — never attempts to solve/bypass anything.
async def _detect_human_verification(page) -> Optional[str]:
    text = ""
    try:
        text = (await page.inner_text("body")).lower()
    except Exception:
        pass

    iframe_srcs = []
    try:
        for handle in await page.query_selector_all("iframe"):
            src = await handle.get_attribute("src")
            if src:
                iframe_srcs.append(src.lower())
    except Exception:
        pass

    def _any_in(haystacks, needles) -> bool:
        return any(n in h for h in haystacks for n in needles)

    combined_text = [text]
    if _any_in(combined_text, _CLOUDFLARE_SIGNATURES):
        return "cloudflare_challenge"
    if _any_in(iframe_srcs, ("recaptcha",)) or _any_in(combined_text, _RECAPTCHA_SIGNATURES):
        return "recaptcha"
    if _any_in(iframe_srcs, ("hcaptcha",)) or _any_in(combined_text, _HCAPTCHA_SIGNATURES):
        return "hcaptcha"
    if _any_in(iframe_srcs, ("turnstile", "challenges.cloudflare.com")) or _any_in(combined_text, _TURNSTILE_SIGNATURES):
        return "turnstile"
    if _any_in(combined_text, _GENERIC_HUMAN_VERIFICATION_SIGNATURES):
        return "unknown_human_verification"
    return None


async def _detect_login_wall(page) -> bool:
    try:
        if await page.query_selector("input[type='password']"):
            return True
        text = (await page.inner_text("body")).lower()
    except Exception:
        return False
    return any(sig in text for sig in _LOGIN_SIGNATURES)


# Never "a business field a human filled in" — excluded from unmapped-field detection.
_NON_BUSINESS_INPUT_TYPES = ("hidden", "submit", "button", "reset", "image")


async def _mapped_field_identifiers(page, adapter: WebFormAdapter) -> set:
    """id/name of every element the adapter's field_mappings resolve to."""
    identifiers = set()
    for mapping in adapter.field_mappings.values():
        el = await page.query_selector(mapping.selector)
        if not el:
            continue
        for attr in ("id", "name"):
            value = await el.get_attribute(attr)
            if value:
                identifiers.add(value)
        if mapping.field_type == "dual_listbox" and mapping.companion_selector:
            # Paired "Selected X" list is also required but filled via the
            # Add click — found via DOM containment, not a hardcoded id.
            try:
                paired_ids = await page.evaluate(
                    """([leftSel, addSel]) => {
                        const left = document.querySelector(leftSel);
                        const add = document.querySelector(addSel);
                        if (!left || !add) return [];
                        let container = left;
                        while (container && !container.contains(add)) {
                            container = container.parentElement;
                        }
                        if (!container) return [];
                        return Array.from(container.querySelectorAll('select[multiple]')).map(s => s.id);
                    }""",
                    [mapping.selector, mapping.companion_selector],
                )
                identifiers.update(sid for sid in paired_ids if sid)
            except Exception:
                pass
    return identifiers


async def _fill_unmapped_visible_email_fields(page, adapter: WebFormAdapter, email_value: str) -> list:
    """Generic rule: any visible input[type=email] not already mapped still
    gets the requester's email, even if the field wasn't required."""
    if not email_value:
        return []
    known = await _mapped_field_identifiers(page, adapter)
    filled = []
    try:
        email_inputs = await page.query_selector_all("input[type='email']")
    except Exception:
        return filled
    for el in email_inputs:
        try:
            if not await el.is_visible():
                continue
            identifier = (await el.get_attribute("id")) or (await el.get_attribute("name")) or ""
            if identifier and identifier in known:
                continue  # already filled by an explicit mapping above
            if await el.input_value():
                continue  # already has a value — don't overwrite
            await el.fill(email_value)
            filled.append(identifier or "email")
        except Exception:
            continue
    return filled


async def _detect_unmapped_required_fields(page, adapter: WebFormAdapter) -> list:
    """A visible required field the adapter never mapped at all (distinct
    from missing_structure, which is a mapped-but-absent field)."""
    known = await _mapped_field_identifiers(page, adapter)
    unmapped = []
    try:
        candidates = await page.query_selector_all("input, textarea, select")
    except Exception:
        return unmapped
    for el in candidates:
        try:
            if not await el.is_visible():
                continue
            input_type = (await el.get_attribute("type") or "").lower()
            if input_type in _NON_BUSINESS_INPUT_TYPES:
                continue
            required_attr = await el.get_attribute("required")
            aria_required = (await el.get_attribute("aria-required") or "").lower()
            if required_attr is None and aria_required != "true":
                continue  # not a required control — not our concern
            # A radio's "name" identifies the whole field, not one option —
            # check group identity first so one mapped option covers it.
            if input_type == "radio":
                identifier = (await el.get_attribute("name")) or (await el.get_attribute("id")) or ""
            else:
                identifier = (await el.get_attribute("id")) or (await el.get_attribute("name")) or ""
            if identifier and identifier in known:
                continue  # this IS one of our mapped fields
            label = identifier or (await el.get_attribute("placeholder")) or input_type or "unnamed field"
            unmapped.append(label)
        except Exception:
            continue
    return unmapped


async def _resolve_exact_option_value(el, value: str, *, fallback_to_other: bool = False) -> Optional[str]:
    """Case-insensitive match, returning the option's own exact value (needed
    since select_option() itself is case-sensitive). fallback_to_other falls
    back to a literal "Other" option instead of failing closed."""
    try:
        options = await el.query_selector_all("option")
    except Exception:
        return None
    other_value = None
    for opt in options:
        opt_value = await opt.get_attribute("value")
        opt_label = (await opt.inner_text()).strip()
        if opt_value == value:
            return opt_value
        if opt_label == value or opt_label.lower() == value.strip().lower():
            return opt_value if opt_value is not None else opt_label
        if fallback_to_other and opt_label.strip().lower() == "other":
            other_value = opt_value if opt_value is not None else opt_label
    return other_value if fallback_to_other else None


async def _detect_unmatched_select_values(page, adapter: WebFormAdapter, inquiry_data: dict) -> list:
    """Pre-flight: does every select mapping's value match an <option>?
    Checked before any fill so a mismatch fails the whole attempt closed."""
    mismatches = []
    for field_name, mapping in adapter.field_mappings.items():
        if mapping.field_type not in ("select", "dual_listbox"):
            continue
        value = _resolve_field_value(mapping, inquiry_data)
        if not value:
            continue
        el = await page.query_selector(mapping.selector)
        if not el or not await el.is_visible():
            continue  # missing_structure/optional-field handling covers this case
        if await _resolve_exact_option_value(el, value, fallback_to_other=mapping.fallback_to_other_option) is None:
            mismatches.append((field_name, value))
    return mismatches


def _missing_inquiry_data(adapter: WebFormAdapter, inquiry_data: dict) -> list:
    """Adapter-defined required mappings are authoritative, independent of
    the page's own HTML `required` attributes."""
    missing = []
    for field_name, mapping in adapter.field_mappings.items():
        if mapping.required and not _resolve_field_value(mapping, inquiry_data):
            missing.append(field_name)
    return missing


def _field_label(field_name: str, mapping: FieldMapping) -> str:
    return mapping.label or field_name


# Bounded: if no listbox appears in this window, fail closed rather than wait forever.
COMBOBOX_LISTBOX_TIMEOUT_MS = 3000


async def _locate_combobox_option(page, selector: str, value: str):
    """Resolves the matching option WITHOUT clicking. Exact-text only;
    zero or 2+ matches fail closed. Returns (option_or_None, reason)."""
    combobox = await page.query_selector(selector)
    if not combobox or not await combobox.is_visible():
        return None, "combobox control not found or not visible"

    try:
        await combobox.click()
        await combobox.fill(value)
    except Exception as e:
        return None, f"could not type into the combobox ({e})"

    # Wait on the option, not the listbox — some widgets (SLDS) have a
    # zero-height listbox whose child overflows visibly.
    try:
        await page.wait_for_selector("[role='listbox'] [role='option']", state="visible", timeout=COMBOBOX_LISTBOX_TIMEOUT_MS)
    except Exception:
        return None, "no listbox of options appeared after typing — this widget does not match the expected combobox behavior"

    try:
        options = []
        for listbox in await page.query_selector_all("[role='listbox']"):
            options.extend(await listbox.query_selector_all("[role='option']"))
    except Exception:
        return None, "could not read the combobox's options"

    # Some widgets duplicate role="option" on a decorative child — same
    # choice, not two; keep only the outermost.
    top_level_options = []
    for opt in options:
        try:
            nested = await opt.evaluate(
                "e => e.parentElement ? e.parentElement.closest('[role=\"option\"]') !== null : false"
            )
        except Exception:
            nested = False
        if not nested:
            top_level_options.append(opt)
    options = top_level_options

    exact_matches = []
    for opt in options:
        if not await opt.is_visible():
            continue
        try:
            # Standard SLDS entity-option markup wraps icon/subtitle text
            # around the real label — prefer that node when present.
            label_el = await opt.query_selector(".slds-listbox__option-text")
            text = (await (label_el or opt).inner_text()).strip()
        except Exception:
            continue
        if text.lower() == value.strip().lower():
            exact_matches.append(opt)

    if not exact_matches:
        return None, f"'{value}' is not an available option in this combobox"
    if len(exact_matches) > 1:
        return None, f"'{value}' matched {len(exact_matches)} options ambiguously — refusing to guess"
    return exact_matches[0], "matched"


async def _detect_unmatched_combobox_values(page, adapter: WebFormAdapter, inquiry_data: dict) -> list:
    """Mirrors _detect_unmatched_select_values for combobox mappings; clears
    the field back to empty afterward, never clicks an option."""
    mismatches = []
    for field_name, mapping in adapter.field_mappings.items():
        if mapping.field_type != "combobox":
            continue
        value = _resolve_field_value(mapping, inquiry_data)
        if not value:
            continue
        option, reason = await _locate_combobox_option(page, mapping.selector, value)
        combobox = await page.query_selector(mapping.selector)
        if combobox:
            try:
                await combobox.fill("")
                await page.keyboard.press("Escape")
            except Exception:
                pass
        if option is None:
            mismatches.append((field_name, reason))
    return mismatches


async def _fill_combobox_field(page, selector: str, value: str):
    """Real fill — called only after a unique match is already confirmed."""
    option, reason = await _locate_combobox_option(page, selector, value)
    if option is None:
        return False, reason
    try:
        await option.click()
    except Exception as e:
        return False, f"found the matching option but could not click it ({e})"
    return True, "matched"


async def run_web_form_automation(
    *,
    target_url: str,
    adapter: WebFormAdapter,
    inquiry_data: dict,
    mode: str,  # "prepare" | "submit"
    target_label: str,  # "manufacturer" | "mock_test"
    # True only once the caller confirms this manufacturer's mi_web_form_url
    # is on file — allow_real_submission alone isn't sufficient.
    target_authorized: bool = True,
) -> WebFormAutomationResult:
    """Runs one automation attempt. Never called for a disabled adapter."""
    if mode == "submit" and not adapter.allow_real_submission:
        # Defense in depth on top of the endpoint-level guard.
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason="Refused: this adapter is not permitted to submit (allow_real_submission=False).",
            target=target_label,
            stage=mode,
        )

    if mode == "submit" and target_label == "manufacturer" and not target_authorized:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=(
                "Refused: this manufacturer has no mi_web_form_url on file. "
                "Web Form submission is only authorized for a manufacturer's own, "
                "on-file form URL."
            ),
            target=target_label,
            stage=mode,
        )

    missing_data = _missing_inquiry_data(adapter, inquiry_data)
    if missing_data:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=f"Missing required inquiry data for: {', '.join(missing_data)}.",
            target=target_label,
            stage=mode,
            missing_fields=missing_data,
        )

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(target_url, wait_until="load", timeout=15000)
            await _dismiss_cookie_banner(page)

            # One explicitly adapter-declared pre-form click, never generic.
            if adapter.pre_form_selector:
                try:
                    gate_el = await page.query_selector(adapter.pre_form_selector)
                    if gate_el and await gate_el.is_visible():
                        # force=True: a radio/checkbox is often covered by
                        # its own <label>, which would otherwise block the click.
                        await gate_el.click(force=True)
                        await page.wait_for_timeout(500)
                except Exception:
                    pass  # checks below fail closed on whatever state results

            await _wait_for_gate_settle(page)

            mechanism = await _detect_human_verification(page)
            if mechanism:
                return WebFormAutomationResult(
                    outcome="human_action_required",
                    reason=f"Human verification detected on the Web Form ({mechanism}).",
                    mechanism=mechanism,
                    target=target_label,
                    stage=mode,
                )

            if await _detect_login_wall(page):
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason="Login/registration wall detected — cannot proceed without credentials.",
                    target=target_label,
                    stage=mode,
                )

            # Every required field must exist/be visible before filling anything.
            missing_structure = []
            for field_name, mapping in adapter.field_mappings.items():
                el = await page.query_selector(mapping.selector)
                visible = await el.is_visible() if el else False
                if mapping.required and not visible:
                    missing_structure.append(field_name)
            if missing_structure:
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason=f"Form structure does not match the configured mapping (missing required field(s): {', '.join(missing_structure)}).",
                    target=target_label,
                    stage=mode,
                    missing_fields=missing_structure,
                )

            # Does the page require a field the adapter never mapped at all?
            unmapped_required = await _detect_unmapped_required_fields(page, adapter)
            if unmapped_required:
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason=(
                        "Unmapped required field(s) detected on the form — the adapter does "
                        f"not know how to fill: {', '.join(unmapped_required)}."
                    ),
                    target=target_label,
                    stage=mode,
                    missing_fields=unmapped_required,
                )

            select_mismatches = await _detect_unmatched_select_values(page, adapter, inquiry_data)
            if select_mismatches:
                first_field, first_value = select_mismatches[0]
                first_label = _field_label(first_field, adapter.field_mappings[first_field])
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason=f"'{first_value}' is not an available option for field '{first_label}'.",
                    target=target_label,
                    stage=mode,
                    missing_fields=[f for f, _ in select_mismatches],
                )

            # Same fail-closed-on-the-whole-attempt guarantee, for combobox
            # mappings (adapter/override-only — see field_type=="combobox").
            combobox_mismatches = await _detect_unmatched_combobox_values(page, adapter, inquiry_data)
            if combobox_mismatches:
                first_field, first_reason = combobox_mismatches[0]
                first_label = _field_label(first_field, adapter.field_mappings[first_field])
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason=f"Combobox field '{first_label}': {first_reason}",
                    target=target_label,
                    stage=mode,
                    missing_fields=[f for f, _ in combobox_mismatches],
                )

            filled = []
            for field_name, mapping in adapter.field_mappings.items():
                value = _resolve_field_value(mapping, inquiry_data)
                if not value:
                    continue
                el = await page.query_selector(mapping.selector)
                if not el or not await el.is_visible():
                    continue  # optional field not present on this form — skip, don't guess
                if mapping.field_type == "checkbox":
                    # "false"/"0"/"no" unchecks, anything else checks;
                    # force=True for the same label-overlap reason as above.
                    should_check = value.lower() not in ("false", "0", "no")
                    if should_check:
                        await page.check(mapping.selector, force=True)
                    else:
                        await page.uncheck(mapping.selector, force=True)
                elif mapping.field_type == "select":
                    # select_option() is case-sensitive — resolve the real
                    # option value first; an unmatched value fails closed.
                    exact_value = await _resolve_exact_option_value(el, value, fallback_to_other=mapping.fallback_to_other_option)
                    if exact_value is None:
                        field_label = _field_label(field_name, mapping)
                        return WebFormAutomationResult(
                            outcome="automation_failed",
                            reason=f"'{value}' is not an available option for field '{field_label}'.",
                            target=target_label,
                            stage=mode,
                            filled_fields=filled,
                            missing_fields=[field_name],
                        )
                    await page.select_option(mapping.selector, value=exact_value)
                elif mapping.field_type == "dual_listbox":
                    # Select in the left <select>, then click the "Add"
                    # control that moves it to the right (submitted) list.
                    exact_value = await _resolve_exact_option_value(el, value, fallback_to_other=mapping.fallback_to_other_option)
                    if exact_value is None:
                        field_label = _field_label(field_name, mapping)
                        return WebFormAutomationResult(
                            outcome="automation_failed",
                            reason=f"'{value}' is not an available option for field '{field_label}'.",
                            target=target_label,
                            stage=mode,
                            filled_fields=filled,
                            missing_fields=[field_name],
                        )
                    await page.select_option(mapping.selector, value=exact_value)
                    await page.click(mapping.companion_selector)
                elif mapping.field_type == "combobox":
                    # Adapter/override-only — see _fill_combobox_field.
                    matched, combobox_reason = await _fill_combobox_field(page, mapping.selector, value)
                    if not matched:
                        field_label = _field_label(field_name, mapping)
                        return WebFormAutomationResult(
                            outcome="automation_failed",
                            reason=f"Combobox field '{field_label}': {combobox_reason}",
                            target=target_label,
                            stage=mode,
                            filled_fields=filled,
                            missing_fields=[field_name],
                        )
                else:
                    await page.fill(mapping.selector, value)
                filled.append(field_name)

            # Safety net, not adapter-specific: fill any visible
            # input[type=email] not already mapped, even if optional.
            unmapped_email_fields = await _fill_unmapped_visible_email_fields(
                page, adapter, inquiry_data.get("requester_email") or ""
            )
            filled.extend(unmapped_email_fields)

            if mode == "prepare":
                return WebFormAutomationResult(
                    outcome="automation_success",
                    reason="Form fields filled and ready for review.",
                    target=target_label,
                    stage=mode,
                    filled_fields=filled,
                )

            # mode == "submit"
            if not adapter.submit_selector:
                return WebFormAutomationResult(
                    outcome="automation_failed",
                    reason="No submit selector configured for this adapter.",
                    target=target_label,
                    stage=mode,
                    filled_fields=filled,
                )

            pre_submit_url = page.url
            try:
                async with page.expect_navigation(timeout=adapter.confirmation_timeout_ms):
                    await page.click(adapter.submit_selector)
            except Exception:
                pass  # no navigation is normal for an SPA/AJAX submit
            post_submit_url = page.url

            # Some sites (Sanofi) only render CAPTCHA inside the submit
            # action, not on load — recheck here.
            post_submit_mechanism = await _detect_human_verification(page)
            if post_submit_mechanism:
                return WebFormAutomationResult(
                    outcome="submitted_but_unverified",
                    reason=f"Human verification appeared after clicking Submit ({post_submit_mechanism}) — cannot confirm whether the manufacturer received the submission.",
                    mechanism=post_submit_mechanism,
                    target=target_label,
                    stage=mode,
                    filled_fields=filled,
                )

            if adapter.error_selector:
                try:
                    err_el = await page.query_selector(adapter.error_selector)
                    if err_el and await err_el.is_visible():
                        err_text = (await err_el.inner_text()).strip()
                        return WebFormAutomationResult(
                            outcome="automation_failed",
                            reason=f"Manufacturer form displayed a submission error: {err_text}" if err_text
                            else "Manufacturer form displayed a submission error.",
                            target=target_label,
                            stage=mode,
                            filled_fields=filled,
                        )
                except Exception:
                    pass

            verified = False
            verified_via_url = False
            if adapter.success_url_contains and adapter.success_url_contains in post_submit_url:
                verified = True
                verified_via_url = True
            if not verified and adapter.confirmation_selector:
                try:
                    await page.wait_for_selector(adapter.confirmation_selector, state="visible", timeout=adapter.confirmation_timeout_ms)
                    verified = True
                except Exception:
                    verified = False

            if verified:
                # Evidence only, never affects the outcome above. A URL only
                # counts if the author verified it's unique per submission.
                confirmation_url = (
                    post_submit_url if (verified_via_url and adapter.confirmation_url_is_unique) else None
                )
                confirmation_screenshot_bytes = None
                if not confirmation_url:
                    try:
                        confirmation_screenshot_bytes = await page.screenshot(full_page=True)
                    except Exception:
                        confirmation_screenshot_bytes = None
                return WebFormAutomationResult(
                    outcome="automation_success",
                    reason="Form submitted and confirmation detected.",
                    target=target_label,
                    stage=mode,
                    filled_fields=filled,
                    confirmation_url=confirmation_url,
                    confirmation_screenshot_bytes=confirmation_screenshot_bytes,
                )

            # Navigated but unconfirmed — distinct from human_action_required:
            # this means we may have ALREADY submitted it.
            if post_submit_url != pre_submit_url:
                return WebFormAutomationResult(
                    outcome="submitted_but_unverified",
                    reason=(
                        "Form was submitted and the page navigated, but the outcome could not be "
                        "verified automatically. Please check the manufacturer's Web Form/portal "
                        "manually to confirm whether the submission already went through before "
                        "resubmitting."
                    ),
                    mechanism=None,
                    target=target_label,
                    stage=mode,
                    filled_fields=filled,
                )

            # (5) Clicked, nothing changed at all — the submission most
            # likely never went through; safe to retry.
            return WebFormAutomationResult(
                outcome="automation_failed",
                reason="Submit was clicked but no confirmation was detected and the page did not navigate.",
                target=target_label,
                stage=mode,
                filled_fields=filled,
            )
        finally:
            await browser.close()


# GENERIC ENGINE — discovery/mapping, additive; never runs for REAL_ADAPTERS entries.
# discover_only() never fills/checks/selects/clicks regardless of any flag.


class MappingConfidence:
    """Plain string constants (not an Enum) so results serialize directly
    into the JSON discovery report without a custom encoder."""
    HIGH_CONFIDENCE = "high_confidence"
    LOW_CONFIDENCE = "low_confidence"
    UNMAPPED = "unmapped"
    UNSUPPORTED = "unsupported"


_CONFIDENCE_RANK = {
    MappingConfidence.HIGH_CONFIDENCE: 2,
    MappingConfidence.LOW_CONFIDENCE: 1,
    MappingConfidence.UNMAPPED: 0,
    MappingConfidence.UNSUPPORTED: 0,
}

# requester_first_name/last_name aren't DB columns — derived via split_requester_name().
GENERIC_INQUIRY_SOURCES = (
    "medication_name",
    "question",
    "requester_name",
    "requester_first_name",
    "requester_last_name",
    "requester_email",
    "team_name",
)

# Anything outside these native (element_type, input_type) pairs is UNSUPPORTED.
_NATIVE_TEXTLIKE_TYPES = {
    ("input", "text"), ("input", ""), ("input", "email"), ("input", "tel"),
    ("textarea", ""),
}
_NATIVE_SELECT_TYPES = {("select", "")}
_NATIVE_CHOICE_TYPES = {("input", "checkbox"), ("input", "radio")}

# Structural ARIA signals marking a native-tag input as a JS-managed widget.
_CUSTOM_WIDGET_ARIA_SIGNALS = ("combobox", "listbox")


def _classify_element_type(tag: str, input_type: str) -> str:
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    return "input"


_SOURCE_RULES = {
    "medication_name": {
        "high_keywords": ("product", "medication name", "medication", "drug name", "drug"),
        "low_keywords": ("med",),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES | _NATIVE_SELECT_TYPES,
    },
    "question": {
        "high_keywords": ("question", "inquiry", "your question", "message", "comments"),
        "low_keywords": (),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
        "high_requires_textarea": True,
    },
    "requester_first_name": {
        "high_keywords": ("first name",),
        "low_keywords": (),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
    },
    "requester_last_name": {
        "high_keywords": ("last name", "surname"),
        "low_keywords": (),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
    },
    "requester_name": {
        "high_keywords": ("full name", "your name", "requester name", "name"),
        "low_keywords": (),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
        # Never let the generic "name" keyword steal a field that's really
        # First/Last Name — those are matched by the two rules above.
        "exclude_keywords": ("first name", "last name", "surname"),
    },
    "requester_email": {
        "high_keywords": ("email address", "email"),
        "low_keywords": (),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
        # input[type=email] is itself a deterministic HIGH signal, no label needed.
        "type_auto_high": "email",
    },
    "team_name": {
        "high_keywords": ("institution", "organization", "facility", "hospital", "team", "practice"),
        "low_keywords": ("company",),
        "eligible_types": _NATIVE_TEXTLIKE_TYPES,
    },
}


@dataclass
class FormField:
    """One discovered DOM control. element_handle is the live Playwright
    handle used for fill/click, excluded from equality/repr."""
    element_type: str  # "input" | "textarea" | "select"
    input_type: str    # "text" | "email" | "tel" | "checkbox" | "radio" | "" (textarea/select)
    id: Optional[str]
    name: Optional[str]
    label_text: Optional[str]
    placeholder: Optional[str]
    aria_label: Optional[str]
    required: bool
    visible: bool
    enabled: bool
    is_custom_widget: bool = False
    options: list = field(default_factory=list)  # list[tuple[value, label]] — select only
    element_handle: object = field(default=None, repr=False, compare=False)

    @property
    def display_label(self) -> str:
        return self.label_text or self.placeholder or self.aria_label or self.name or self.id or "unnamed field"

    def best_selector(self) -> Optional[str]:
        """Valid for the rest of THIS page session; None if no id/name."""
        if self.id:
            return f"#{_css_escape(self.id)}"
        if self.name:
            return f'[name="{_css_escape(self.name)}"]'
        return None


def _css_escape(value: str) -> str:
    # Minimal, not a full CSS.escape() polyfill — known limitation.
    return value.replace('"', '\\"')


@dataclass
class MappingResult:
    field: "FormField"
    source_key: Optional[str]
    confidence: str  # MappingConfidence.*
    reason: str


def _field_signal_sources(f: FormField) -> list:
    """Priority order: label > name/id > aria-label > placeholder."""
    return [
        ("label", (f.label_text or "").strip().lower()),
        ("name/id", " ".join(filter(None, [(f.name or "").lower(), (f.id or "").lower()])).strip()),
        ("aria-label", (f.aria_label or "").strip().lower()),
        ("placeholder", (f.placeholder or "").strip().lower()),
    ]


def _classify_field_for_source(f: FormField, source_key: str):
    """Returns (confidence, reason) or None if this source doesn't apply to
    this field at all (wrong type, or no signal matched)."""
    rule = _SOURCE_RULES[source_key]
    type_key = (f.element_type, f.input_type)
    auto_high_type = rule.get("type_auto_high")

    if type_key not in rule["eligible_types"] and f.input_type != auto_high_type:
        return None

    if auto_high_type and f.input_type == auto_high_type:
        return (MappingConfidence.HIGH_CONFIDENCE, f"structural match: input[type={auto_high_type}]")

    exclude = rule.get("exclude_keywords", ())

    def excluded(text: str) -> bool:
        return any(x in text for x in exclude)

    high_kw = rule["high_keywords"]
    low_kw = rule.get("low_keywords", ())
    requires_textarea_for_high = rule.get("high_requires_textarea", False)

    # Label / name-id / aria-label: strong signals.
    for src_name, text in _field_signal_sources(f):
        if src_name == "placeholder" or not text or excluded(text):
            continue
        if any(k in text for k in high_kw):
            if requires_textarea_for_high and f.element_type != "textarea":
                return (MappingConfidence.LOW_CONFIDENCE, f"{src_name} matched {text!r} but element is not a textarea")
            return (MappingConfidence.HIGH_CONFIDENCE, f"{src_name} matched {text!r}")

    # Placeholder: weaker signal per the approved priority order — counts
    # only as LOW_CONFIDENCE even on an exact keyword hit.
    placeholder = (f.placeholder or "").strip().lower()
    if placeholder and not excluded(placeholder) and any(k in placeholder for k in high_kw):
        return (MappingConfidence.LOW_CONFIDENCE, f"placeholder matched {placeholder!r}")

    # Loose/semantic keyword match anywhere — LOW_CONFIDENCE only.
    for src_name, text in _field_signal_sources(f):
        if text and not excluded(text) and any(k in text for k in low_kw):
            return (MappingConfidence.LOW_CONFIDENCE, f"{src_name} loosely matched {text!r}")

    return None


def map_fields(fields: list) -> list:
    """Pure/deterministic. checkbox/radio and no-id/name fields -> UNMAPPED;
    custom widgets -> UNSUPPORTED; 2+ HIGH matches for one source -> both demoted."""
    results = []
    candidates = {k: [] for k in GENERIC_INQUIRY_SOURCES}

    for f in fields:
        if f.input_type in ("checkbox", "radio"):
            results.append(MappingResult(
                field=f, source_key=None, confidence=MappingConfidence.UNMAPPED,
                reason="checkbox/radio fields are never generically mapped — constant-value only, via adapter override",
            ))
            continue
        if f.is_custom_widget:
            results.append(MappingResult(
                field=f, source_key=None, confidence=MappingConfidence.UNSUPPORTED,
                reason="custom widget (combobox/listbox ARIA pattern) — not supported by the generic engine in V1",
            ))
            continue
        if not f.best_selector():
            results.append(MappingResult(
                field=f, source_key=None, confidence=MappingConfidence.UNMAPPED,
                reason="no id or name attribute — no durable selector to map generically",
            ))
            continue

        best = None
        for source_key in GENERIC_INQUIRY_SOURCES:
            m = _classify_field_for_source(f, source_key)
            if m is None:
                continue
            confidence, reason = m
            if best is None or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[best[1]]:
                best = (source_key, confidence, reason)

        if best is None:
            results.append(MappingResult(
                field=f, source_key=None, confidence=MappingConfidence.UNMAPPED,
                reason="no Inquiry source's keyword/type signals matched",
            ))
        else:
            source_key, confidence, reason = best
            candidates[source_key].append((f, confidence))
            results.append(MappingResult(field=f, source_key=source_key, confidence=confidence, reason=reason))

    for source_key, cands in candidates.items():
        high = [c for c in cands if c[1] == MappingConfidence.HIGH_CONFIDENCE]
        if len(high) > 1:
            for i, r in enumerate(results):
                if r.source_key == source_key and r.confidence == MappingConfidence.HIGH_CONFIDENCE:
                    results[i] = MappingResult(
                        field=r.field, source_key=None, confidence=MappingConfidence.UNMAPPED,
                        reason=f"ambiguous: {len(high)} fields matched '{source_key}' at HIGH_CONFIDENCE — refusing to guess",
                    )

    return results


def build_generic_field_mappings(mapping_results: list) -> dict:
    """Only HIGH_CONFIDENCE results become a FieldMapping — enforces
    "LOW_CONFIDENCE never auto-fills"."""
    mappings = {}
    for r in mapping_results:
        if r.confidence != MappingConfidence.HIGH_CONFIDENCE or not r.source_key:
            continue
        f = r.field
        selector = f.best_selector()
        if not selector:
            continue
        field_type = "select" if f.element_type == "select" else "text"
        mappings[r.source_key] = FieldMapping(
            selector=selector,
            source_key=r.source_key,
            required=f.required,
            field_type=field_type,
            label=f.display_label,
        )
    return mappings


# Reads the RENDERED DOM (post-JS). Read-only: no fill/check/select/click.
async def discover_fields(page) -> list:
    fields = []
    try:
        candidates = await page.query_selector_all(
            "input, textarea, select, [role='combobox'], [role='listbox']"
        )
    except Exception:
        return fields

    for el in candidates:
        try:
            tag = (await el.evaluate("e => e.tagName")).lower()
            role = (await el.get_attribute("role") or "").lower()

            if tag not in ("input", "textarea", "select"):
                # Non-native widget container (e.g. <div role="combobox">) — stub so a required one still fails closed.
                required_attr = await el.get_attribute("required")
                aria_required = (await el.get_attribute("aria-required") or "").lower()
                fields.append(FormField(
                    element_type="custom", input_type="",
                    id=await el.get_attribute("id"), name=await el.get_attribute("name"),
                    label_text=None, placeholder=None, aria_label=await el.get_attribute("aria-label"),
                    required=(required_attr is not None or aria_required == "true"),
                    visible=await el.is_visible(), enabled=True,
                    is_custom_widget=True, element_handle=el,
                ))
                continue

            input_type = (await el.get_attribute("type") or "").lower() if tag == "input" else ""
            if input_type in _NON_BUSINESS_INPUT_TYPES:
                continue  # hidden/submit/button/reset/image — not a "field"

            aria_autocomplete = (await el.get_attribute("aria-autocomplete") or "").lower()
            aria_haspopup = (await el.get_attribute("aria-haspopup") or "").lower()
            is_custom_widget = (
                role in _CUSTOM_WIDGET_ARIA_SIGNALS
                or bool(aria_autocomplete)
                or aria_haspopup in _CUSTOM_WIDGET_ARIA_SIGNALS
            )

            element_id = await el.get_attribute("id")
            label_text = await el.evaluate(
                """e => {
                    if (e.id) {
                        try {
                            const lbl = document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
                            if (lbl) return lbl.textContent.trim();
                        } catch (err) {}
                    }
                    const wrapping = e.closest('label');
                    if (wrapping) return wrapping.textContent.trim();
                    return null;
                }"""
            )
            required_attr = await el.get_attribute("required")
            aria_required = (await el.get_attribute("aria-required") or "").lower()

            options = []
            if tag == "select":
                try:
                    for opt in await el.query_selector_all("option"):
                        opt_value = await opt.get_attribute("value")
                        opt_label = (await opt.inner_text()).strip()
                        options.append((opt_value, opt_label))
                except Exception:
                    pass

            fields.append(FormField(
                element_type=_classify_element_type(tag, input_type),
                input_type=input_type,
                id=element_id,
                name=await el.get_attribute("name"),
                label_text=label_text,
                placeholder=await el.get_attribute("placeholder"),
                aria_label=await el.get_attribute("aria-label"),
                required=(required_attr is not None or aria_required == "true"),
                visible=await el.is_visible(),
                enabled=not bool(await el.get_attribute("disabled")),
                is_custom_widget=is_custom_widget,
                options=options,
                element_handle=el,
            ))
        except Exception:
            continue

    return fields


async def _discover_submit_candidates(page) -> list:
    """Plausible submit buttons/inputs, for ambiguity detection. Never clicked."""
    candidates = []
    try:
        els = await page.query_selector_all("button, input[type=submit], input[type=button]")
    except Exception:
        return candidates
    for el in els:
        try:
            if not await el.is_visible():
                continue
            el_type = (await el.get_attribute("type") or "").lower()
            text = ((await el.inner_text()) or (await el.get_attribute("value")) or "").strip().lower()
            looks_like_submit = el_type == "submit" or any(
                kw in text for kw in ("submit", "send", "send message", "send request")
            )
            if looks_like_submit:
                candidates.append(el)
        except Exception:
            continue
    return candidates


async def _structural_verification_evidence(page) -> bool:
    """Informational only — never authorizes submission or builds verification."""
    try:
        form = await page.query_selector("form[action]")
        if form:
            action = (await form.get_attribute("action") or "").strip()
            if action and action != "#":
                return True
    except Exception:
        pass
    try:
        hit = await page.evaluate(
            """() => {
                const kws = ['success', 'confirm', 'thank'];
                const els = document.querySelectorAll('[id], [class]');
                for (const e of els) {
                    const hay = ((e.id || '') + ' ' + (e.className || '')).toLowerCase();
                    if (kws.some(k => hay.includes(k))) return true;
                }
                return false;
            }"""
        )
        return bool(hit)
    except Exception:
        return False


def _generic_missing_required(mapping_results: list) -> list:
    """Any required, visible field lacking a HIGH_CONFIDENCE mapping blocks the attempt."""
    missing = []
    for r in mapping_results:
        f = r.field
        if f.required and f.visible and r.confidence != MappingConfidence.HIGH_CONFIDENCE:
            missing.append(f.display_label)
    return missing


@dataclass
class DiscoveryReport:
    target_url: str
    reachable: bool = False
    error: Optional[str] = None
    human_gate_mechanism: Optional[str] = None
    login_gate: bool = False
    discovered_field_count: int = 0
    high_confidence_sources: list = field(default_factory=list)
    low_confidence_sources: list = field(default_factory=list)
    unmapped_required_count: int = 0
    unsupported_required_count: int = 0
    ambiguous_sources: list = field(default_factory=list)
    submit_candidate_count: int = 0
    prepare_capable: bool = False
    submit_capable_evidence: bool = False


async def discover_only(target_url: str, *, pre_form_selector: Optional[str] = None) -> DiscoveryReport:
    """Read-only harness: navigate, detect gates, discover, map, report.
    Never fills/checks/selects/submits — only an optional pre_form_selector click."""
    report = DiscoveryReport(target_url=target_url)
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(target_url, wait_until="load", timeout=20000)
                report.reachable = True
                await _dismiss_cookie_banner(page)

                if pre_form_selector:
                    try:
                        gate_el = await page.query_selector(pre_form_selector)
                        if gate_el and await gate_el.is_visible():
                            await gate_el.click()
                            await page.wait_for_timeout(500)
                    except Exception:
                        pass

                await _wait_for_gate_settle(page)

                mechanism = await _detect_human_verification(page)
                if mechanism:
                    report.human_gate_mechanism = mechanism
                    return report

                if await _detect_login_wall(page):
                    report.login_gate = True
                    return report

                fields = await discover_fields(page)
                report.discovered_field_count = len(fields)
                mapping_results = map_fields(fields)

                seen_sources = set()
                ambiguous = set()
                for r in mapping_results:
                    if r.confidence == MappingConfidence.HIGH_CONFIDENCE and r.source_key:
                        if r.source_key in seen_sources:
                            ambiguous.add(r.source_key)
                        seen_sources.add(r.source_key)
                        if r.source_key not in report.high_confidence_sources:
                            report.high_confidence_sources.append(r.source_key)
                    elif r.confidence == MappingConfidence.LOW_CONFIDENCE and r.source_key:
                        if r.source_key not in report.low_confidence_sources:
                            report.low_confidence_sources.append(r.source_key)
                    elif r.confidence == MappingConfidence.UNSUPPORTED and r.field.required and r.field.visible:
                        report.unsupported_required_count += 1
                    elif (
                        r.confidence == MappingConfidence.UNMAPPED
                        and r.field.required
                        and r.field.visible
                        and not r.field.is_custom_widget
                        and r.field.input_type not in ("checkbox", "radio")
                    ):
                        report.unmapped_required_count += 1
                report.ambiguous_sources = sorted(ambiguous)

                missing_required = _generic_missing_required(mapping_results)
                submit_candidates = await _discover_submit_candidates(page)
                report.submit_candidate_count = len(submit_candidates)
                report.submit_capable_evidence = await _structural_verification_evidence(page)
                report.prepare_capable = not missing_required

                return report
            finally:
                await browser.close()
    except Exception as e:
        report.reachable = False
        report.error = str(e)[:300]
        return report


async def run_generic_web_form_automation(
    *,
    target_url: str,
    inquiry_data: dict,
    mode: str,
    target_label: str,
    override_adapter: Optional[WebFormAdapter] = None,
    # See run_web_form_automation — same DB-derived authorization contract.
    target_authorized: bool = True,
) -> WebFormAutomationResult:
    """Adapter-free path: runs discovery, then hands off to unmodified
    run_web_form_automation() with a synthetic adapter (override wins ties)."""
    pre_form_selector = override_adapter.pre_form_selector if override_adapter else None
    discovery = await discover_only(target_url, pre_form_selector=pre_form_selector)

    if not discovery.reachable:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=f"Could not reach the Web Form: {discovery.error or 'unknown error'}.",
            target=target_label, stage=mode,
        )
    if discovery.human_gate_mechanism:
        return WebFormAutomationResult(
            outcome="human_action_required",
            reason=f"Human verification detected on the Web Form ({discovery.human_gate_mechanism}).",
            mechanism=discovery.human_gate_mechanism,
            target=target_label, stage=mode,
        )
    if discovery.login_gate:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason="Login/registration wall detected — cannot proceed without credentials.",
            target=target_label, stage=mode,
        )

    # Re-run the field pass live (discover_only doesn't return FormFields, to
    # keep the report JSON-serializable) to build the synthetic adapter.
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(target_url, wait_until="load", timeout=20000)
            await _dismiss_cookie_banner(page)
            if pre_form_selector:
                try:
                    gate_el = await page.query_selector(pre_form_selector)
                    if gate_el and await gate_el.is_visible():
                        await gate_el.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass
            fields = await discover_fields(page)
        finally:
            await browser.close()

    mapping_results = map_fields(fields)
    generic_mappings = build_generic_field_mappings(mapping_results)
    override_mappings = override_adapter.field_mappings if override_adapter else {}
    merged_mappings = {**generic_mappings, **override_mappings}

    missing_required = _generic_missing_required(mapping_results)
    # Fields an override explicitly maps are exempt from this check.
    missing_required = [
        label for label in missing_required
        if not any(m.label == label for m in override_mappings.values())
    ]
    if missing_required:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=(
                "Generic discovery could not confidently map required field(s): "
                f"{', '.join(missing_required)}."
            ),
            target=target_label, stage=mode,
            missing_fields=missing_required,
        )

    has_verification = bool(
        override_adapter and (
            override_adapter.confirmation_selector
            or override_adapter.error_selector
            or override_adapter.success_url_contains
        )
    )
    if mode == "submit" and not has_verification:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=(
                "Refused: no verified submission mechanism is configured for this manufacturer. "
                "The generic engine will not click Submit merely to see what happens — an adapter "
                "override must supply an explicit success/error verification strategy first."
            ),
            target=target_label, stage=mode,
        )
    # Second, independent gate for manufacturer targets only —
    # authorization is DB-derived, not a static hostname list.
    if mode == "submit" and target_label == "manufacturer" and not target_authorized:
        return WebFormAutomationResult(
            outcome="automation_failed",
            reason=(
                "Refused: this manufacturer has no mi_web_form_url on file. "
                "A verification mechanism alone is not enough to submit in "
                "production — the target must also be a known, on-file form URL."
            ),
            target=target_label, stage=mode,
        )

    submit_selector = None
    if override_adapter and override_adapter.submit_selector:
        submit_selector = override_adapter.submit_selector
    else:
        submit_candidates = await _discover_submit_candidates_for_selector(target_url, pre_form_selector)
        if len(submit_candidates) == 1:
            submit_selector = submit_candidates[0]

    synthetic_adapter = WebFormAdapter(
        key=f"generic:{urlparse(target_url).hostname or target_url}",
        label="Generic discovery engine",
        url_match=None,
        automation_enabled=True,
        allow_real_submission=(
            has_verification and (target_label != "manufacturer" or target_authorized)
        ),
        field_mappings=merged_mappings,
        submit_selector=submit_selector,
        confirmation_selector=override_adapter.confirmation_selector if override_adapter else None,
        error_selector=override_adapter.error_selector if override_adapter else None,
        success_url_contains=override_adapter.success_url_contains if override_adapter else None,
        confirmation_timeout_ms=override_adapter.confirmation_timeout_ms if override_adapter else 3000,
        pre_form_selector=pre_form_selector,
    )

    return await run_web_form_automation(
        target_url=target_url,
        adapter=synthetic_adapter,
        inquiry_data=inquiry_data,
        mode=mode,
        target_label=target_label,
        target_authorized=target_authorized,
    )


async def _discover_submit_candidates_for_selector(target_url: str, pre_form_selector: Optional[str]) -> list:
    """Same as discover_only's candidates, but returns id/name selector
    strings instead of live handles. Read-only, never clicks."""
    from playwright.async_api import async_playwright

    selectors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(target_url, wait_until="load", timeout=20000)
            await _dismiss_cookie_banner(page)
            if pre_form_selector:
                try:
                    gate_el = await page.query_selector(pre_form_selector)
                    if gate_el and await gate_el.is_visible():
                        await gate_el.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass
            candidates = await _discover_submit_candidates(page)
            for el in candidates:
                el_id = await el.get_attribute("id")
                el_name = await el.get_attribute("name")
                if el_id:
                    selectors.append(f"#{_css_escape(el_id)}")
                elif el_name:
                    selectors.append(f'[name="{_css_escape(el_name)}"]')
        finally:
            await browser.close()
    return selectors
