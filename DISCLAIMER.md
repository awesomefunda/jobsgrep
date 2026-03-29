# JobsGrep — Legal Disclaimer

## What This Tool Does

JobsGrep is a personal job search aggregation tool that queries **publicly available APIs** to discover job postings. It does **not** scrape websites that prohibit automated access (except in LOCAL mode with explicit user consent).

## Data Sources and Their Terms of Service

| Source | API Type | ToS Reference |
|--------|----------|---------------|
| Greenhouse | Public API (no auth) | https://boards-api.greenhouse.io |
| Lever | Public API (no auth) | https://hire.lever.co/developer/postings |
| Ashby | Public API (no auth) | https://developers.ashbyhq.com |
| Hacker News (Algolia) | Official public API | https://hn.algolia.com/api |
| YC Companies (yc-oss) | Community-maintained OSS | https://github.com/yc-oss/api |
| USAJobs | Official government API | https://developer.usajobs.gov/terms |
| JobSpy | Web scraping (LOCAL only) | Use at your own risk |

## Your Responsibilities

By using this tool, you agree that:

1. **You are responsible for compliance** with each data source's Terms of Service.
2. **Scraping features** (JobSpy, LinkedIn, etc.) are provided for **LOCAL, personal use only**. Using them to build databases, commercial products, or to circumvent paywalls may violate ToS.
3. **This tool is not affiliated with** Greenhouse, Lever, Ashby, LinkedIn, or any other platform.
4. **No warranty**: This software is provided "as is" without warranty of any kind.
5. **Rate limiting**: You agree not to modify the rate limits in ways that could harm third-party services.

## Privacy

- In **LOCAL** mode: all data stays on your machine.
- In **PRIVATE** mode: data is processed on your self-hosted server and deleted when you choose.
- In **PUBLIC** mode: generated Excel reports are automatically deleted after download or after 1 hour, whichever comes first. No job data is stored persistently.

## User-Agent Policy

This tool sends an honest User-Agent string: `JobsGrep/1.0 (personal job search tool; contact: {your-email})`. It never impersonates a browser.

## Contact

If you believe this tool is accessing your API in a way that violates your terms, please open an issue on the project repository.
