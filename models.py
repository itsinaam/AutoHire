from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Jobs(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)

    description = Column(Text)

    annual_budget = Column(String(255), nullable=False)

    # comma separated skills
    primary_skills = Column(String(500), nullable=False)

    # comma separated keywords
    target_keywords = Column(String(500), nullable=False)

    # single linkedin location id
    location = Column(String(50), nullable=False)

    min_experience = Column(String(255), nullable=False)

    connection_degree = Column(String(255), nullable=False)

    status = Column(String(50), nullable=False, default="inactive")


class UserProfile(Base):
    __tablename__ = "users_profile"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    profile_url = Column(String(500), nullable=False)
    name = Column(String(255), nullable=False, default="")
    headline = Column(Text)
    location = Column(String(255))
    about = Column(Text)
    experience = Column(Text, nullable=False, default="[]")
    raw_profile = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ShortListedUserProfile(Base):
    __tablename__ = "short_listed_users_profiles"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    profile_url = Column(String(500), nullable=False)
    name = Column(String(255), nullable=False, default="")
    headline = Column(Text)
    location = Column(String(255))
    about = Column(Text)
    experience = Column(Text, nullable=False, default="[]")
    matched_skills = Column(Text, nullable=False, default="")
    match_score = Column(Integer, nullable=False, default=0)
    shortlist_reason = Column(Text)
    raw_profile = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)