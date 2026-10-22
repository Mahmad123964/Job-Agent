Extract the job description from the email below. Use ONLY information literally present in the email. Do not add, infer, or embellish requirements.

Set usable=true only if the email states a specific role AND at least some concrete responsibilities or requirements. A bare "we're hiring, send your CV" with no role details is usable=false.

Return:
- usable: bool
- role_title: string or null
- company: string or null
- responsibilities: list of strings quoted/paraphrased closely from the email
- requirements: list of strings quoted/paraphrased closely from the email
- summary: 1-2 sentences describing the role, from the email only

=== EMAIL ===
From: {sender}
Subject: {subject}

{body}
