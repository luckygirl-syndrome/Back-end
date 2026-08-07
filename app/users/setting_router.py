from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.core.security import hash_password, verify_password
from app.core.observability import posthog_client
from app.users import models, schemas
from app.users.router import get_current_user, _verify_token_by_provider, _extract_social_info

router = APIRouter(prefix="/api", tags=["설정"])

_200 = lambda result: {"200": {"content": {"application/json": {"example": {"isSuccess": True, "code": "200", "message": "OK", "result": result}}}}}


@router.get("/setting/account", summary="계정 정보 조회", responses=_200({"email": "user@example.com", "socialProviders": ["google"], "hasPassword": False}))
def get_account(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    providers = db.query(models.UserSocialProvider).filter_by(user_id=current_user.user_id).all()
    provider_list = [p.provider for p in providers]
    if current_user.social_provider and current_user.social_provider not in provider_list:
        provider_list.append(current_user.social_provider)
    return success({
        "email": current_user.email,
        "socialProviders": provider_list,
        "hasPassword": bool(current_user.password),
    })


@router.patch("/setting/nickname", summary="닉네임 수정", responses=_200({"nickname": "새닉네임"}))
def update_nickname(data: schemas.NicknameUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.nickname = data.nickname
    db.commit()
    db.refresh(current_user)
    return success({"nickname": current_user.nickname})


@router.patch("/setting/height-weight", summary="키와 몸무게 수정", responses=_200({"height": 165, "weight": 55}))
def update_height_weight(data: schemas.HeightWeightUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    current_user.height = data.height
    current_user.weight = data.weight
    db.commit()
    db.refresh(current_user)
    return success({
        "height": current_user.height,
        "weight": current_user.weight,
    })


@router.post("/setting/social/link", summary="소셜 계정 연동", responses=_200(None))
def link_social(body: schemas.SocialLinkRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    already = db.query(models.UserSocialProvider).filter_by(
        user_id=current_user.user_id, provider=body.provider
    ).first()
    if already:
        raise HTTPException(status_code=400, detail=f"이미 {body.provider} 계정이 연결되어 있습니다.")

    payload = _verify_token_by_provider(body.provider, body.id_token)
    info = _extract_social_info(body.provider, payload)
    social_id = info["social_id"]
    if not social_id:
        raise HTTPException(status_code=400, detail="소셜 토큰에서 유저 정보를 가져올 수 없습니다.")

    conflict = db.query(models.UserSocialProvider).filter_by(
        provider=body.provider, social_id=social_id
    ).first()
    if conflict:
        raise HTTPException(status_code=400, detail="이미 다른 계정에 연결된 소셜 계정입니다.")

    db.add(models.UserSocialProvider(user_id=current_user.user_id, provider=body.provider, social_id=social_id))
    db.commit()
    return success(message=f"{body.provider} 계정이 연결되었습니다.")


@router.delete("/setting/social/unlink", summary="소셜 계정 해제", responses=_200(None))
def unlink_social(body: schemas.SocialUnlinkRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    providers = db.query(models.UserSocialProvider).filter_by(user_id=current_user.user_id).all()
    has_password = bool(current_user.password)

    if len(providers) <= 1 and not has_password:
        raise HTTPException(status_code=400, detail="마지막 로그인 수단입니다. 해제할 수 없습니다.")

    link = db.query(models.UserSocialProvider).filter_by(
        user_id=current_user.user_id, provider=body.provider
    ).first()
    if not link:
        raise HTTPException(status_code=400, detail="연결된 계정이 없습니다.")

    db.delete(link)
    db.commit()
    return success(message=f"{body.provider} 연결이 해제되었습니다.")


@router.post("/setting/password/verify", summary="현재 비밀번호 확인", responses=_200(None))
def verify_password_endpoint(body: schemas.PasswordVerify, current_user: models.User = Depends(get_current_user)):
    if not current_user.password:
        raise HTTPException(status_code=400, detail="소셜 로그인 계정은 비밀번호가 없습니다.")
    if not verify_password(body.current_password, current_user.password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    return success(message="비밀번호 확인 완료.")


@router.patch("/setting/password", summary="비밀번호 변경", responses=_200(None))
def change_password(body: schemas.PasswordChange, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.password:
        raise HTTPException(status_code=400, detail="소셜 로그인 계정은 비밀번호가 없습니다.")
    current_user.password = hash_password(body.new_password)
    db.commit()
    return success(message="비밀번호가 변경되었습니다.")


@router.post("/setting/inquiry", summary="문의하기", responses=_200(None))
def create_inquiry(body: schemas.InquiryCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    inquiry = models.Inquiry(user_id=current_user.user_id, content=body.content, reply_email=body.reply_email)
    db.add(inquiry)
    db.commit()
    return success(message="문의가 접수되었습니다.")
