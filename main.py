import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, status
from database import SessionLocal, engine, get_db
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from models import Base, Jobs, ShortListedUserProfile, UserProfile

app = FastAPI(title="LinkedIn Profile Scraper API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all origins (FE ports/domains)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScrapeRequest(BaseModel):
	search_keyword: str = Field(min_length=1)
	location_id: str = Field(min_length=1)
	limit: int = Field(ge=1, le=50)
	headless: bool = False


class ScrapeResponse(BaseModel):
	search_keyword: str
	location_id: str
	limit: int
	saved_to: str
	total_profiles: int
	profiles: list[dict[str, Any]]


class BackgroundScrapeResponse(BaseModel):
	message: str


class JobScrapeRequest(BaseModel):
	id: int = Field(ge=1)
	status: str = Field(min_length=1, max_length=50)


class JobBase(BaseModel):
	name: str = Field(min_length=1, max_length=255)
	description: str | None = None
	annual_budget: str = Field(min_length=1, max_length=255)
	primary_skills: str = Field(min_length=1, max_length=500)
	target_keywords: str = Field(min_length=1, max_length=500)
	location: str = Field(min_length=1, max_length=50)
	min_experience: str = Field(min_length=1, max_length=255)
	connection_degree: str = Field(min_length=1, max_length=255)
	status: str = Field(default="inactive", min_length=1, max_length=50)


class JobCreate(JobBase):
	pass


class JobUpdate(BaseModel):
	name: str | None = Field(default=None, min_length=1, max_length=255)
	description: str | None = None
	annual_budget: str | None = Field(default=None, min_length=1, max_length=255)
	primary_skills: str | None = Field(default=None, min_length=1, max_length=500)
	target_keywords: str | None = Field(default=None, min_length=1, max_length=500)
	location: str | None = Field(default=None, min_length=1, max_length=50)
	min_experience: str | None = Field(default=None, min_length=1, max_length=255)
	connection_degree: str | None = Field(default=None, min_length=1, max_length=255)
	status: str | None = Field(default=None, min_length=1, max_length=50)


class JobResponse(JobBase):
	id: int

	model_config = ConfigDict(from_attributes=True)


def create_all_tables() -> None:
	Base.metadata.create_all(bind=engine)


def get_job_or_404(db: Session, job_id: int) -> Jobs:
	job = db.get(Jobs, job_id)
	if job is None:
		raise HTTPException(status_code=404, detail="Job not found.")
	return job


def commit_and_refresh(db: Session, instance: Jobs) -> Jobs:
	try:
		db.commit()
		db.refresh(instance)
		return instance
	except SQLAlchemyError as exc:
		db.rollback()
		raise HTTPException(status_code=500, detail="Database operation failed.") from exc


def apply_job_updates(job: Jobs, payload: JobUpdate) -> Jobs:
	for field_name, field_value in payload.model_dump(exclude_unset=True).items():
		setattr(job, field_name, field_value)
	return job


def load_profiles_from_json(file_path: str) -> list[dict[str, Any]]:
	try:
		with open(file_path, "r", encoding="utf-8") as file_handle:
			data = json.load(file_handle)
			if isinstance(data, list):
				return [profile for profile in data if isinstance(profile, dict)]
	except FileNotFoundError:
		return []
	except json.JSONDecodeError:
		return []
	return []


def load_system_prompt(file_path: str = "system_prompt.txt") -> str:
	with open(file_path, "r", encoding="utf-8") as file_handle:
		prompt = file_handle.read().strip()
	if not prompt:
		raise RuntimeError("system_prompt.txt is empty.")
	return prompt


def extract_json_object(content: str) -> dict[str, Any]:
	content = content.strip()
	if content.startswith("```"):
		content = re.sub(r"^```(?:json)?\s*", "", content)
		content = re.sub(r"\s*```$", "", content)
	data = json.loads(content)
	if not isinstance(data, dict):
		raise RuntimeError("OpenAI shortlisting response must be a JSON object.")
	return data


def call_openai_shortlist(job: Jobs, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
	api_key = os.getenv("OPENAI_API_KEY")
	if not api_key:
		raise RuntimeError("OPENAI_API_KEY is not configured.")

	system_prompt = load_system_prompt()
	user_payload = {
		"job": {
			"id": job.id,
			"name": job.name,
			"description": job.description,
			"annual_budget": job.annual_budget,
			"primary_skills": job.primary_skills,
			"target_keywords": job.target_keywords,
			"location": job.location,
			"min_experience": job.min_experience,
			"connection_degree": job.connection_degree,
		},
		"profiles": profiles,
		"required_output": {
			"shortlisted_profiles": [
				{
					"profile_url": "string",
					"matched_skills": ["string"],
					"match_score": "integer from 0 to 100",
					"shortlist_reason": "string",
				}
			],
		},
	}
	print("[jobs/scrape] Final system prompt for OpenAI:")
	print(system_prompt)
	print("[jobs/scrape] Final user payload for OpenAI:")
	print(json.dumps(user_payload, ensure_ascii=False, indent=2))
	request_payload = {
		"model": "gpt-4o",
		"response_format": {"type": "json_object"},
		"messages": [
			{
				"role": "system",
				"content": system_prompt,
			},
			{
				"role": "user",
				"content": json.dumps(user_payload, ensure_ascii=False),
			},
		],
	}

	request = urllib.request.Request(
		"https://api.openai.com/v1/chat/completions",
		data=json.dumps(request_payload).encode("utf-8"),
		headers={
			"Authorization": f"Bearer {api_key}",
			"Content-Type": "application/json",
		},
		method="POST",
	)

	try:
		with urllib.request.urlopen(request, timeout=120) as response:
			response_data = json.loads(response.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		error_body = exc.read().decode("utf-8", errors="ignore")
		raise RuntimeError(f"OpenAI shortlisting request failed: {error_body}") from exc
	except urllib.error.URLError as exc:
		raise RuntimeError(f"OpenAI shortlisting request failed: {exc.reason}") from exc

	message_content = response_data["choices"][0]["message"]["content"]
	parsed_response = extract_json_object(message_content)
	shortlisted = parsed_response.get("shortlisted_profiles", [])
	print("[jobs/scrape] Raw OpenAI shortlist response:")
	print(json.dumps(parsed_response, ensure_ascii=False, indent=2))
	if not isinstance(shortlisted, list):
		raise RuntimeError("OpenAI shortlisting response must contain shortlisted_profiles list.")
	return shortlisted


def build_shortlisted_profiles(profiles: list[dict[str, Any]], shortlisted: list[dict[str, Any]]) -> list[dict[str, Any]]:
	profiles_by_url = {
		str(profile.get("profile_url", "")): profile
		for profile in profiles
		if str(profile.get("profile_url", ""))
	}
	built_shortlist: list[dict[str, Any]] = []

	for item in shortlisted:
		if not isinstance(item, dict):
			continue
		profile_url = str(item.get("profile_url", "")).strip()
		profile = profiles_by_url.get(profile_url)
		if profile is None:
			continue
		matched_skills = item.get("matched_skills", [])
		if not isinstance(matched_skills, list):
			matched_skills = []
		match_score = item.get("match_score", 0)
		try:
			match_score = int(match_score)
		except (TypeError, ValueError):
			match_score = 0
		built_shortlist.append(
			{
				"profile": profile,
				"matched_skills": [str(skill) for skill in matched_skills if str(skill).strip()],
				"match_score": max(0, min(match_score, 100)),
				"shortlist_reason": str(item.get("shortlist_reason", "")).strip(),
			}
		)

	built_shortlist.sort(key=lambda item: item["match_score"], reverse=True)
	return built_shortlist


def replace_job_profiles(db: Session, job_id: int, profiles: list[dict[str, Any]]) -> None:
	db.query(UserProfile).filter(UserProfile.job_id == job_id).delete(synchronize_session=False)
	for profile in profiles:
		db.add(
			UserProfile(
				job_id=job_id,
				profile_url=str(profile.get("profile_url", "")),
				name=str(profile.get("name", "")),
				headline=profile.get("headline"),
				location=profile.get("location"),
				about=profile.get("about"),
				experience=json.dumps(profile.get("experience", []), ensure_ascii=False),
				raw_profile=json.dumps(profile, ensure_ascii=False),
			)
		)
	db.commit()


def replace_shortlisted_profiles(db: Session, job_id: int, shortlisted_profiles: list[dict[str, Any]]) -> None:
	db.query(ShortListedUserProfile).filter(ShortListedUserProfile.job_id == job_id).delete(synchronize_session=False)
	for item in shortlisted_profiles:
		profile = item["profile"]
		db.add(
			ShortListedUserProfile(
				job_id=job_id,
				profile_url=str(profile.get("profile_url", "")),
				name=str(profile.get("name", "")),
				headline=profile.get("headline"),
				location=profile.get("location"),
				about=profile.get("about"),
				experience=json.dumps(profile.get("experience", []), ensure_ascii=False),
				matched_skills=", ".join(item.get("matched_skills", [])),
				match_score=int(item.get("match_score", 0)),
				shortlist_reason=item.get("shortlist_reason"),
				raw_profile=json.dumps(profile, ensure_ascii=False),
			)
		)
	db.commit()


def run_job_scrape_in_background(job_id: int, search_keyword: str, location_id: str) -> None:
	from scraper import run_scraper_sync

	db = SessionLocal()

	try:
		print(f"[jobs/scrape] Background scrape started for job_id={job_id}")
		result = run_scraper_sync(
			search_keyword=search_keyword,
			location_id=location_id,
			limit=10,
			headless=False,
		)

		job = db.query(Jobs).filter(Jobs.id == job_id).first()
		profiles = load_profiles_from_json(result.get("saved_to", "linkedin_profiles.json"))
		print(
			f"[jobs/scrape] Profiles loaded for job_id={job_id}. "
			f"count={len(profiles)} file={result.get('saved_to', 'linkedin_profiles.json')}"
		)
		if job:
			replace_job_profiles(db, job_id, profiles)
			print(f"[jobs/scrape] Starting OpenAI shortlisting for job_id={job_id}")
			openai_shortlist = call_openai_shortlist(job, profiles)
			print(
				f"[jobs/scrape] OpenAI shortlisting completed for job_id={job_id}. "
				f"selected={len(openai_shortlist)}"
			)
			shortlisted_profiles = build_shortlisted_profiles(profiles, openai_shortlist)
			print("[jobs/scrape] Final mapped shortlist result:")
			print(json.dumps(shortlisted_profiles, ensure_ascii=False, indent=2))
			replace_shortlisted_profiles(db, job_id, shortlisted_profiles)
			print(
				f"[jobs/scrape] Shortlisted profiles saved for job_id={job_id}. "
				f"saved={len(shortlisted_profiles)}"
			)

		job = db.query(Jobs).filter(Jobs.id == job_id).first()
		if job:
			job.status = "active"
			db.commit()
			db.refresh(job)
			print(f"[jobs/scrape] Job marked active for job_id={job_id}")

	except Exception as exc:
		print(f"[jobs/scrape] Background scrape failed for job_id={job_id}: {exc}")
		job = db.query(Jobs).filter(Jobs.id == job_id).first()
		if job:
			job.status = "failed"
			db.commit()
			db.refresh(job)

	finally:
		db.close()


@app.get("/")
async def health_check():
	return {"message": "LinkedIn scraper API is running."}


@app.post("/scrape-linkedin-profiles", response_model=ScrapeResponse)
async def scrape_linkedin_profiles(request: ScrapeRequest):
	try:
		from scraper import run_scraper_sync
		return await asyncio.to_thread(
			run_scraper_sync,
			search_keyword=request.search_keyword,
			location_id=request.location_id,
			limit=request.limit,
			headless=request.headless,
		)
	except RuntimeError as exc:
		raise HTTPException(status_code=401, detail=str(exc)) from exc
	except Exception as exc:
		raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/jobs/scrape", response_model=BackgroundScrapeResponse)
async def scrape_job_profiles(
    request: JobScrapeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = get_job_or_404(db, request.id)

    # ❌ if already running
    if job.status.strip().lower() == "processing":
        raise HTTPException(status_code=400, detail="Job is already being scraped.")

    # ❌ if already completed/active
    if job.status.strip().lower() == "active":
        raise HTTPException(status_code=400, detail="Job already scraped.")

    # ✅ set to processing BEFORE starting background task
    job.status = "processing"
    db.commit()
    db.refresh(job)

    background_tasks.add_task(
        run_job_scrape_in_background,
        job_id=job.id,
        search_keyword=job.name,
        location_id=job.location,
    )

    return {
        "message": "Scraping started. Results will be available in ~5 minutes."
    }

	


@app.post("/create-tables")
async def create_tables():
	await asyncio.to_thread(create_all_tables)
	return {"message": "Database tables created successfully."}



@app.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(job: JobCreate, db: Session = Depends(get_db)):
	db_job = Jobs(**job.model_dump())
	db.add(db_job)
	return commit_and_refresh(db, db_job)


@app.get("/jobs", response_model=list[JobResponse])
def list_jobs(
	skip: int = Query(default=0, ge=0),
	limit: int = Query(default=20, ge=1, le=100),
	db: Session = Depends(get_db),
):
	return db.query(Jobs).order_by(Jobs.id.desc()).offset(skip).limit(limit).all()


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
	return get_job_or_404(db, job_id)


@app.put("/jobs/{job_id}", response_model=JobResponse)
def update_job(job_id: int, payload: JobUpdate, db: Session = Depends(get_db)):
	job = get_job_or_404(db, job_id)
	updated_job = apply_job_updates(job, payload)
	return commit_and_refresh(db, updated_job)


@app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, db: Session = Depends(get_db)):
	job = get_job_or_404(db, job_id)
	try:
		db.delete(job)
		db.commit()
	except SQLAlchemyError as exc:
		db.rollback()
		raise HTTPException(status_code=500, detail="Database operation failed.") from exc

	return Response(status_code=status.HTTP_204_NO_CONTENT)

