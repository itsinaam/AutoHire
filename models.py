from sqlalchemy import Column, Integer, String, Text
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