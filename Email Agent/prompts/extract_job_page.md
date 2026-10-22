Below is the text of a single web page fetched from {url}. Decide whether it is a job posting and, if so, pull out what is literally on the page. Use ONLY information present in the text. Do not infer, guess, or add anything.

Return:
- is_job_posting: true only if the page describes a specific open role (title plus some responsibilities/requirements). Login walls, search result lists, error pages and generic company pages are false.
- role_title: the job title exactly as shown, or null
- company: the hiring company name exactly as shown, or null
- description: the full job description text (summary, responsibilities, requirements, benefits) cleaned of navigation, cookie banners, footers and unrelated listings. Keep the original wording. Empty string if not a job posting.
- poster_name: the name of the person shown on the page as the poster / recruiter / hiring contact for THIS posting (e.g. "Posted by", "Meet the hiring team", "Contact: Jane Doe"), only if such a person is explicitly shown. Otherwise null. Never use author names from unrelated articles or testimonials.

=== PAGE TEXT ===
{page_text}
