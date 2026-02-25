from typing import Dict, Any
from app.db.db import SessionLocal
from app.models.user_profile import UserProfile, BenefitsPreference
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_users() -> Dict[str, Any]:
    """Load users data from the app.db database."""    
    session = SessionLocal()
    try:
        users = session.query(UserProfile).all()
        return {user.id: user.to_dict() for user in users}
    finally:
        session.close()

def get_user_by_username(username: str) -> UserProfile | None:
    """Fetch a user by username from the database."""
    session = SessionLocal()
    try:
        return session.query(UserProfile).filter(UserProfile.username == username).first()
    finally:
        session.close()

def get_next_available_user_id() -> int:
    """Get the next available user_id, which is +1 from the latest user_id in the user_profile tab in app.db"""    
    session = SessionLocal()
    try:
        max_user = session.query(UserProfile).order_by(UserProfile.id.desc()).first()
        next_id = (max_user.id + 1) if max_user else 1
        return next_id
    finally:
        session.close()

def create_user(username: str, password: str, name: str | None = None, email: str | None = None, benefits_preference: str | None = None) -> Dict[str, Any]:
    """Create a new user in the database. Returns user data as dictionary."""
    session = SessionLocal()
    try:
        # Check if username already exists
        existing_user = session.query(UserProfile).filter(UserProfile.username == username).first()
        if existing_user:
            raise ValueError("Username already exists")
        
        # Map benefits_preference string to enum
        pref_enum = BenefitsPreference.No_preference
        if benefits_preference:
            try:
                pref_enum = BenefitsPreference(benefits_preference)
            except ValueError:
                pref_enum = BenefitsPreference.No_preference
        
        # Normalize empty strings to None for optional fields (prevents UNIQUE constraint violations)
        normalized_name = name.strip() if name and name.strip() else None
        normalized_email = email.strip() if email and email.strip() else None
        
        # get the id for this user
        generated_id = get_next_available_user_id()
        
        # Hash the password before storing
        hashed_password = hash_password(password)
        
        # Create new user
        new_user = UserProfile(
            id=generated_id,
            username=username,
            password_hash=hashed_password,
            name=normalized_name,
            email=normalized_email,
            benefits_preference=pref_enum
        )
        session.add(new_user)
        session.commit()
        return new_user.to_dict()
    finally:
        session.close()

def login_user():
    """
    Check if the user keyed in the right credentials
    """
