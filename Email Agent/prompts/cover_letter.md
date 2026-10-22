Write a professional cover letter from the candidate to the recruiter for the job below.

STRICT RULES:
1. Mention only skills, tools, and experience that appear in the CANDIDATE PROFILE. Never add skills the candidate does not list.
2. Reference the role, responsibilities, and requirements only as stated in the JOB DESCRIPTION. Never invent requirements.
3. Where a JD requirement is not covered by the candidate's profile, simply do not claim it. Do not apologise for it either.
4. Tone: confident, concise, specific. 3-4 short paragraphs, 180-280 words. Plain text, no markdown.
5. Open with "Dear Hiring Manager," (or "Dear {recruiter_name}," if a recruiter name is given). Close with "Best regards," then the candidate's name and current role on separate lines.
6. Do not include placeholders, brackets, or notes to the user.

If you cannot write a letter that satisfies all rules (e.g. the JD and profile have no meaningful overlap, or the JD is too thin), set confident=false and leave letter empty.

Return: confident (bool), letter (string)

=== JOB DESCRIPTION ===
Role: {role_title}
Company: {company}
Summary: {summary}
Responsibilities:
{responsibilities}
Requirements:
{requirements}

=== CANDIDATE PROFILE (from active CV) ===
Name: {name}
Current role: {current_role}
Core skill areas: {core_skill_areas}
Key skills: {key_skills}
Experience: {experience_summary}

Recruiter name (if known): {recruiter_name}
