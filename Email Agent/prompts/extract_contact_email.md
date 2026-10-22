The job posting below was fetched from {url}. The addresses in CANDIDATES are the ONLY email addresses that literally appear on that page.

Pick the one address (if any) that a candidate should send an application to: a recruiter, HR, hiring manager, talent team or "apply to / send your CV to" address that is clearly tied to this posting.

STRICT RULES:
1. Return only an address from CANDIDATES, copied exactly. Never construct, guess, complete or alter an address.
2. If none of the candidates is an application/recruiting contact (e.g. privacy@, support@, press@, noreply@, a generic unsubscribe or legal address, or an address belonging to an unrelated company/advert), return email = null.
3. If it is ambiguous which candidate is the recruiting contact, return null rather than guessing.

Return: email (string or null), reason (one short sentence).

=== CANDIDATES ===
{candidates}

=== JOB POSTING ===
{description}
