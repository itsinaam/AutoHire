import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import engine, get_db
from models import Base, Jobs
# from scraper import LIMIT, LOCATION_ID, OUTPUT_FILE, PARALLEL_WORKERS, SEARCH_KEYWORD, run_scraper_sync


app = FastAPI(title="LinkedIn Profile Scraper API")


# class ScrapeRequest(BaseModel):
# 	search_keyword: str = Field(default=SEARCH_KEYWORD, min_length=1)
# 	location_id: str = Field(default=LOCATION_ID, min_length=1)
# 	limit: int = Field(default=LIMIT, ge=1, le=50)
# 	output_file: str = Field(default=OUTPUT_FILE, min_length=1)
# 	parallel_workers: int = Field(default=PARALLEL_WORKERS, ge=1, le=10)
# 	headless: bool = False


# class ScrapeResponse(BaseModel):
# 	search_keyword: str
# 	location_id: str
# 	limit: int
# 	saved_to: str
# 	total_profiles: int
# 	profiles: list[dict[str, Any]]


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


@app.get("/")
async def health_check():
	return {"message": "LinkedIn scraper API is running."}


# @app.post("/scrape-linkedin-profiles", response_model=ScrapeResponse)
# async def scrape_linkedin_profiles(request: ScrapeRequest):
# 	try:
# 		return await asyncio.to_thread(
# 			run_scraper_sync,
# 			search_keyword=request.search_keyword,
# 			location_id=request.location_id,
# 			limit=request.limit,
# 			output_file=request.output_file,
# 			parallel_workers=request.parallel_workers,
# 			headless=request.headless,
# 		)
# 	except RuntimeError as exc:
# 		raise HTTPException(status_code=401, detail=str(exc)) from exc
# 	except Exception as exc:
# 		raise HTTPException(status_code=500, detail=str(exc)) from exc


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
