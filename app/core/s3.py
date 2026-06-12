import uuid
import boto3
from botocore.exceptions import ClientError
from app.core.config import settings

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
    return _s3_client


def upload_image(file_bytes: bytes, mime_type: str, folder: str = "products") -> str:
    ext = mime_type.split("/")[-1].replace("jpeg", "jpg")
    key = f"{folder}/{uuid.uuid4().hex}.{ext}"
    get_s3_client().put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=mime_type,
    )
    return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{key}"


def delete_image(url: str) -> None:
    try:
        key = url.split(".amazonaws.com/")[-1]
        get_s3_client().delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
    except ClientError:
        pass
