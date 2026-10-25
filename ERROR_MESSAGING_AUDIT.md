# PlantWise V4 — Error Messaging & Security Leak Audit

## Overview
This audit certifies that PlantWise V4 adheres strictly to a zero-technical-leak policy. All user communications, system exceptions, validation failures, and network interruptions are handled through a unified, accessible toast notification and error contract. Under no circumstances are Python tracebacks, database drivers, SQL queries, internal paths, or API secrets revealed to the end user.

---

## 1. Unified Error Contract Specification

For all JSON API endpoints, error responses conform strictly to:
```json
{
  "ok": false,
  "code": "VALIDATION_ERROR | NOT_FOUND | UNAUTHORIZED | SERVER_BUSY",
  "message": "User-friendly, non-technical explanation in English or Kiswahili",
  "ref_code": "Optional 8-character trace token for customer support lookup"
}
```

For traditional page navigations, unexpected HTTP errors (400, 403, 404, 429, 500) render `templates/error.html`:
- Friendly illustration and calm heading.
- Contextual advice on how the farmer can proceed (e.g. Return to Dashboard or Check Connection).
- An incident reference code (e.g. `PW-E-8F2B1A09`) that logs into server logs without exposing internal details to the browser.

---

## 2. Bilingual Toast Notification Matrix

| Scenario / Trigger | Toast Type | English Text | Kiswahili Text | Auto-Dismiss |
| :--- | :---: | :--- | :--- | :---: |
| **Network Disconnected (Offline)** | `warning` | Network connection lost. Offline features remain active. | Mtandao umekatika. Vipengele vya nje ya mtandao vinaendelea. | Persistent until reconnected |
| **Network Restored (Online)** | `success` | Back online. Your connection is restored. | Umeunganishwa tena kwenye mtandao. | 4 seconds |
| **CSRF Token Expired / Invalid** | `warning` | Your session timed out. Please refresh the page and try again. | Muda wa kipindi chako umekwisha. Tafadhali onyesha upya ukurasa. | 8 seconds |
| **Rate Limit Exceeded (429)** | `warning` | Too many requests. Please wait a moment before trying again. | Maombi mengi mno. Tafadhali subiri kidogo kabla ya kujaribu tena. | 6 seconds |
| **Invalid Login Credentials** | `error` | Incorrect email or password. Please try again. | Barua pepe au nenosiri si sahihi. Tafadhali jaribu tena. | 6 seconds |
| **Account Created Successfully** | `success` | Welcome to PlantWise! Your account is active. | Karibu PlantWise! Akaunti yako imewashwa rasmi. | 5 seconds |
| **Task Completed / Toggled** | `success` | Task updated successfully. | Kazi imesasishwa kikamilifu. | 4 seconds |
| **Task Postponed** | `info` | Task rescheduled to new date. | Kazi imeahirishwa hadi tarehe mpya. | 4 seconds |
| **Produce Listed on Market** | `success` | Your produce listing is now live in the marketplace. | Mazao yako sasa yapo sokoni kwa wanunuzi. | 5 seconds |
| **Order Placed Successfully** | `success` | Order placed! The seller has been notified. | Agizo limewekwa! Muuzaji amearifiwa. | 5 seconds |
| **Oversell Prevention** | `error` | Requested quantity exceeds available stock. | Kiasi unachotaka kinazidi kile kilichopo ghalani. | 6 seconds |
| **Self-Order Prevented** | `error` | You cannot purchase your own produce listing. | Huwezi kuagiza au kununua mazao yako mwenyewe. | 6 seconds |
| **Image Upload Too Large (>10MB)**| `error` | Image is too large. It will be compressed before upload. | Picha ni kubwa mno. Itapunguzwa ukubwa kabla ya kupakia. | 5 seconds |
| **Invalid Image Format** | `error` | Please select a valid photo (JPEG, PNG, or WebP). | Tafadhali chagua picha halisi (JPEG, PNG, au WebP). | 5 seconds |
| **Diagnosis AI Unavailable** | `info` | AI network busy. Providing certified Knowledge Base diagnosis. | Mtandao wa AI unashughuli. Tunatumia mwongozo wa msingi wa kilimo. | 7 seconds |
| **Missing County / Crop Field** | `error` | Please select your county and crop to proceed. | Tafadhali chagua kaunti na zao lako ili kuendelea. | 5 seconds |

---

## 3. Automated Security Leak Scan Results

The automated security scanner (`scratch/leak_scan.py`) executed live against all endpoints, including error injectors and boundary conditions.

| Sensitive Pattern Checked | Occurrences Detected in HTML/JSON Responses | Status |
| :--- | :---: | :---: |
| `Traceback (most recent call last)` | **0** | ✅ Clean |
| `psycopg2.` driver errors | **0** | ✅ Clean |
| `OperationalError:` / `DatabaseError` | **0** | ✅ Clean |
| `UndefinedTable:` / `UndefinedColumn:` | **0** | ✅ Clean |
| `InFailedSqlTransaction` | **0** | ✅ Clean |
| `OPENROUTER_API_KEY` / `sk-or-v1-` | **0** | ✅ Clean |
| Password Hashes (`pbkdf2:sha256:`, `$2b$`) | **0** | ✅ Clean |
| Internal Server File Paths (`C:\Users\...`) | **0** | ✅ Clean |

---

## 4. Toast Engine Verification
The unified accessible notification engine (`static/js/toast.js` and `static/css/toast.css`):
1. **WCAG 2.1 AA Compliance**:
   - High contrast status icons and text.
   - Live region announcing via `role="status"` and `aria-live="polite"`.
   - Critical alerts via `role="alert"` and `aria-live="assertive"`.
2. **Mobile Usability**:
   - Pause auto-dismiss timer on touch hold or desktop mouse hover.
   - Swipe dismiss gesture for touchscreens.
   - Keyboard dismissal with `Escape` key.
   - Full offline/online auto-detection with Kiswahili support.
3. **Legacy Elimination**:
   - All legacy blocking JavaScript `alert()` modal calls across the entire codebase were systematically eliminated and replaced with non-blocking toast notifications.
