from pydantic import BaseModel
from typing import Optional


class ProductReviewCreate(BaseModel):
    is_returned: bool
    satisfaction: Optional[str] = None  # "너무별로", "조금별로", "이정도면괜찮죠", "최고에요"
    review: Optional[str] = None
