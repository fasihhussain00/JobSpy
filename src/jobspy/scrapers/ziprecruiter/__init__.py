"""
jobspy.scrapers.ziprecruiter
~~~~~~~~~~~~~~~~~~~

This module contains routines to scrape ZipRecruiter.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import List, Optional, Tuple, Any

from concurrent.futures import ThreadPoolExecutor

from .. import Scraper, ScraperInput, Site
from ..utils import (
    logger,
    extract_emails_from_text,
    create_session,
    markdown_converter,
)
from ...jobs import (
    JobPost,
    Compensation,
    Location,
    JobResponse,
    JobType,
    Country,
    DescriptionFormat,
)


class ZipRecruiterScraper(Scraper):
    base_url = "https://www.ziprecruiter.com"
    api_url = "https://api.ziprecruiter.com"

    def __init__(self, proxies: Optional[str] = None):
        """
        Initializes ZipRecruiterScraper with the ZipRecruiter job search url
        """
        self.scraper_input = None
        super().__init__(Site.ZIP_RECRUITER, proxies=proxies)
        self.session = create_session(self.proxies)
        self._get_cookies()

        self.delay = 5
        self.jobs_per_page = 20
        self.seen_urls = set()

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        """
        Scrapes ZipRecruiter for jobs with scraper_input criteria.
        :param scraper_input: Information about job search criteria.
        :return: JobResponse containing a list of jobs.
        """
        self.scraper_input = scraper_input
        job_list: list[JobPost] = []
        continue_token = None

        max_pages = math.ceil(scraper_input.results_wanted / self.jobs_per_page)
        for page in range(1, max_pages + 1):
            if len(job_list) >= scraper_input.results_wanted:
                break
            if page > 1:
                time.sleep(self.delay)
            logger.info(f"ZipRecruiter search page: {page}")
            jobs_on_page, continue_token = self._find_jobs_in_page(
                scraper_input, continue_token
            )
            if jobs_on_page:
                job_list.extend(jobs_on_page)
            else:
                break
            if not continue_token:
                break
        return JobResponse(jobs=job_list[: scraper_input.results_wanted])

    def _find_jobs_in_page(
        self, scraper_input: ScraperInput, continue_token: str | None = None
    ) -> Tuple[list[JobPost], Optional[str]]:
        """
        Scrapes a page of ZipRecruiter for jobs with scraper_input criteria
        :param scraper_input:
        :param continue_token:
        :return: jobs found on page
        """
        jobs_list = []
        params = self._add_params(scraper_input)
        if continue_token:
            params["continue_from"] = continue_token
        try:
            res = self.session.get(
                f"{self.api_url}/jobs-app/jobs", headers=self.headers, params=params
            )
            if res.status_code not in range(200, 400):
                if res.status_code == 429:
                    err = "429 Response - Blocked by ZipRecruiter for too many requests"
                else:
                    err = f"ZipRecruiter response status code {res.status_code}"
                    err += f" with response: {res.text}"  # ZipRecruiter likely not available in EU
                logger.error(err)
                return jobs_list, ""
        except Exception as e:
            if "Proxy responded with" in str(e):
                logger.error(f"Indeed: Bad proxy")
            else:
                logger.error(f"Indeed: {str(e)}")
            return jobs_list, ""

        res_data = res.json()
        jobs_list = res_data.get("jobs", [])
        next_continue_token = res_data.get("continue", None)
        with ThreadPoolExecutor(max_workers=self.jobs_per_page) as executor:
            job_results = [executor.submit(self._process_job, job) for job in jobs_list]

        job_list = list(filter(None, (result.result() for result in job_results)))
        return job_list, next_continue_token

    def _process_job(self, job: dict) -> JobPost | None:
        """
        Processes an individual job dict from the response
        """
        title = job.get("name")
        job_url = f"{self.base_url}/jobs//j?lvk={job['listing_key']}"
        if job_url in self.seen_urls:
            return
        self.seen_urls.add(job_url)

        description = job.get("job_description", "").strip()
        description = (
            markdown_converter(description)
            if self.scraper_input.description_format == DescriptionFormat.MARKDOWN
            else description
        )
        company = job.get("hiring_company", {}).get("name")
        country_value = "usa" if job.get("job_country") == "US" else "canada"
        country_enum = Country.from_string(country_value)

        location = Location(
            city=job.get("job_city"), state=job.get("job_state"), country=country_enum
        )
        job_type = self._get_job_type_enum(
            job.get("employment_type", "").replace("_", "").lower()
        )
        date_posted = datetime.fromisoformat(job["posted_time"].rstrip("Z")).date()
        comp_interval = job.get("compensation_interval")
        comp_interval = "yearly" if comp_interval == "annual" else comp_interval
        comp_min = int(job["compensation_min"]) if "compensation_min" in job else None
        comp_max = int(job["compensation_max"]) if "compensation_max" in job else None
        comp_currency = job.get("compensation_currency")
        return JobPost(
            id=str(job['listing_key']),
            title=title,
            company_name=company,
            location=location,
            job_type=job_type,
            compensation=Compensation(
                interval=comp_interval,
                min_amount=comp_min,
                max_amount=comp_max,
                currency=comp_currency,
            ),
            date_posted=date_posted,
            job_url=job_url,
            description=description,
            emails=extract_emails_from_text(description) if description else None,
        )

    def _get_cookies(self):
        data = "event_type=session&logged_in=false&number_of_retry=1&property=model%3AiPhone&property=os%3AiOS&property=locale%3Aen_us&property=app_build_number%3A4734&property=app_version%3A91.0&property=manufacturer%3AApple&property=timestamp%3A2024-01-12T12%3A04%3A42-06%3A00&property=screen_height%3A852&property=os_version%3A16.6.1&property=source%3Ainstall&property=screen_width%3A393&property=device_model%3AiPhone%2014%20Pro&property=brand%3AApple"
        url = f"{self.api_url}/jobs-app/event"
        self.session.post(url, data=data, headers=self.headers)

    @staticmethod
    def _get_job_type_enum(job_type_str: str) -> list[JobType] | None:
        for job_type in JobType:
            if job_type_str in job_type.value:
                return [job_type]
        return None

    @staticmethod
    def _add_params(scraper_input) -> dict[str, str | Any]:
        params = {
            "search": scraper_input.search_term,
            "location": scraper_input.location,
        }
        if scraper_input.hours_old:
            params["days"] = max(scraper_input.hours_old // 24, 1)
        job_type_map = {JobType.FULL_TIME: "full_time", JobType.PART_TIME: "part_time"}
        if scraper_input.job_type:
            job_type = scraper_input.job_type
            params["employment_type"] = job_type_map.get(job_type, job_type.value[0])
        if scraper_input.easy_apply:
            params["zipapply"] = 1
        if scraper_input.is_remote:
            params["remote"] = 1
        if scraper_input.distance:
            params["radius"] = scraper_input.distance
        return {k: v for k, v in params.items() if v is not None}

    headers = {
        "Host": "api.ziprecruiter.com",
        "accept": "*/*",
        "x-zr-zva-override": "100000000;vid:ZT1huzm_EQlDTVEc",
        "x-pushnotificationid": "0ff4983d38d7fc5b3370297f2bcffcf4b3321c418f5c22dd152a0264707602a0",
        "x-deviceid": "D77B3A92-E589-46A4-8A39-6EF6F1D86006",
        "user-agent": "Job Search/87.0 (iPhone; CPU iOS 16_6_1 like Mac OS X)",
        "authorization": "Basic YTBlZjMyZDYtN2I0Yy00MWVkLWEyODMtYTI1NDAzMzI0YTcyOg==",
        "accept-language": "en-US,en;q=0.9",
    }
