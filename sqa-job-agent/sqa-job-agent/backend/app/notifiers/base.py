from ..models import Job


class BaseNotifier:
    name: str = "base"

    def send(self, job: Job) -> None:
        raise NotImplementedError

    @staticmethod
    def _eligibility(job: Job) -> str:
        region = getattr(job, "eligible_region", None) or "Unclear"
        country = getattr(job, "eligible_country", None)
        if region == "Specific country" and country:
            return f"{country} only"
        return region

    def format_message(self, job: Job) -> str:
        lines = [
            f"New SQA job ({job.score}/100): {job.title}",
            f"Company: {job.company or 'Unknown'}",
            f"Location: {job.location}",
            f"Seniority: {job.seniority or 'unknown'}"
            + (" · Pakistan-eligible" if job.pakistan_eligible else "")
            + (" · Worldwide" if job.is_worldwide else ""),
            "Eligible region: " + self._eligibility(job),
        ]
        if job.salary_min or job.salary_max:
            currency = job.salary_currency or ""
            low = f"{job.salary_min:,.0f}" if job.salary_min else "?"
            high = f"{job.salary_max:,.0f}" if job.salary_max else "?"
            lines.append(f"Salary: {currency} {low} - {high}".strip())
        skills = list(job.required_skills or [])
        if skills:
            lines.append("Skills: " + ", ".join(skills[:8]))
        lines.append(f"Source: {job.source}")
        lines.append(job.url)
        return "\n".join(lines)
