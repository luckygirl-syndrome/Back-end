# app/users/service.py
from sqlalchemy.orm import Session
from .models import User
import json

class UserService:
    def get_user_context(self, db: Session, user_id: int):
        user = db.query(User).filter(User.user_id == user_id).first()
        from app.chat.constants import DEFAULT_VALUES
        
        if not user:
            return {
                "persona_type": DEFAULT_VALUES["persona_type"],
                "frequent_malls": DEFAULT_VALUES["malls"],
                "target_style": DEFAULT_VALUES["target_style"]
            }
            
        malls = []
        if user.favorite_shops:
            try:
                malls = json.loads(user.favorite_shops) if user.favorite_shops.startswith("[") else [user.favorite_shops]
            except:
                malls = [user.favorite_shops]

        return {
            "persona_type": user.fbti_type or DEFAULT_VALUES["persona_type"],
            "frequent_malls": malls,
            "target_style": user.style or DEFAULT_VALUES["target_style"]
        }
