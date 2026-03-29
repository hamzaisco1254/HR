"""Data models for HR documents"""
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class Employee:
    """Employee information model"""
    name: str
    gender: str  # "Madame" or "Monsieur"
    cin: str
    cin_place: str
    cin_date: datetime
    position: str
    start_date: datetime


@dataclass
class Company:
    """Company information model"""
    name: str = "PLW Tunisia"
    legal_id: str = ""
    representative_name: str = "Lobna MILED"
    address: str = ""
    city: str = "Tunis"


@dataclass
class DocumentConfig:
    """Document generation configuration"""
    employee: Employee
    company: Company
    reference: str
    document_type: str  # "attestation" or "ordre_mission"
    current_date: Optional[datetime] = None

    def __post_init__(self):
        if self.current_date is None:
            self.current_date = datetime.now()
