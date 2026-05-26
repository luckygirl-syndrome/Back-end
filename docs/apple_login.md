# 애플 로그인 구현 가이드

> 대상: 백엔드 / 플러터 프론트 팀원

---

## 전체 흐름

```
[Flutter 앱]                        [백엔드]                    [Apple 서버]
     |                                  |                            |
     | 1. Sign in with Apple 요청       |                            |
     |----------------------------------------->  Apple ID 인증     |
     |                                  |          <----------------|
     | 2. id_token 수신                 |                            |
     |                                  |                            |
     | 3. POST /api/auth/apple          |                            |
     |   { "id_token": "..." }          |                            |
     | -------------------------------->|                            |
     |                                  | 4. Apple 공개키 조회       |
     |                                  |--------------------------->|
     |                                  |    JWKS 반환              |
     |                                  |<--------------------------|
     |                                  | 5. id_token 서명 검증     |
     |                                  | 6. DB 조회/생성           |
     |                                  |                            |
     | 7. access_token, is_new_user     |                            |
     |<---------------------------------|                            |
```

---

## 백엔드

### 토큰 검증 방식 (JWKS)

애플은 카카오와 동일한 **JWKS(JSON Web Key Set)** 방식을 사용합니다.  
플러터가 애플에서 받은 `id_token`을 백엔드로 전달하면, 백엔드가 아래 과정으로 검증합니다.

1. `https://appleid.apple.com/auth/keys` 에서 Apple 공개키 목록 조회
2. `id_token` 헤더의 `kid`(Key ID)와 일치하는 공개키 선택
3. 해당 공개키로 서명 검증 + `aud`(우리 앱 Bundle ID) + `iss`(`https://appleid.apple.com`) 확인
4. 검증 통과 시 `sub`(애플 유저 고유 ID), `email` 추출

### 유저 식별 전략 — 중요

**애플 로그인의 핵심 제약: 이메일은 최초 로그인 1회만 제공됩니다.**

| 로그인 | `sub` | `email` |
|--------|-------|---------|
| 최초 | O | O (유저가 숨기기 선택 가능) |
| 2회 이후 | O | X |

따라서 유저는 `sub`(= DB의 `social_id` 컬럼)으로 식별합니다.  
이메일로 조회하면 2회차 로그인부터 실패하므로 절대 사용하지 않습니다.

### 환경변수

`.env`에 추가:

```
APPLE_CLIENT_ID=com.yourcompany.yourapp
```

- iOS 네이티브 앱 → **Bundle ID** (예: `com.ttobaba.app`)
- 웹 → Apple Developer Console의 **Service ID**

---

## API 스펙

### `POST /api/auth/apple` — 로그인 / 회원가입

**Request Body**

```json
{
  "id_token": "eyJraWQiOiJ..."
}
```

**Response 200**

```json
{
  "status": "success",
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "is_new_user": true
}
```

> `is_new_user`가 `true`이면 닉네임 설정 화면으로 이동시켜야 합니다.  
> 애플은 이름을 제공하지 않으므로 닉네임이 항상 빈 문자열로 생성됩니다.

**Response 401**

```json
{ "detail": "유효하지 않은 애플 토큰입니다." }
```

**Response 400**

```json
{ "detail": "이미 해당 이메일로 가입된 계정입니다. 기존 로그인 방식을 이용해주세요." }
```

---

### `POST /api/auth/apple/connect` — 기존 계정에 애플 연결

이미 이메일 / 구글 / 카카오로 로그인된 유저가 애플 계정을 추가 연결할 때 사용합니다.

**Request Header**

```
Authorization: <access_token>
```

**Request Body**

```json
{
  "id_token": "eyJraWQiOiJ..."
}
```

**Response 200**

```json
{
  "status": "success",
  "message": "애플 계정이 연결되었습니다."
}
```

**Response 400**

```json
{ "detail": "이미 애플 계정이 연결되어 있습니다." }
{ "detail": "이미 다른 계정에 연결된 애플 계정입니다." }
```

---

## 플러터 (프론트)

### 필요한 패키지

`pubspec.yaml`에 추가:

```yaml
dependencies:
  sign_in_with_apple: ^6.0.0
```

### 구현 코드

```dart
import 'package:sign_in_with_apple/sign_in_with_apple.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';

Future<void> signInWithApple(BuildContext context) async {
  // 1. 애플 인증 요청
  final credential = await SignInWithApple.getAppleIDCredential(
    scopes: [
      AppleIDAuthorizationScopes.email,
      AppleIDAuthorizationScopes.fullName,
    ],
  );

  // 2. id_token 추출
  final idToken = credential.identityToken;
  if (idToken == null) return;

  // 3. 백엔드로 전달
  final response = await http.post(
    Uri.parse('https://your-api.com/api/auth/apple'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({'id_token': idToken}),
  );

  final data = jsonDecode(response.body);

  if (response.statusCode == 200) {
    final accessToken = data['access_token'];
    final isNewUser = data['is_new_user'] as bool;

    // 토큰 저장 (flutter_secure_storage 등)
    await saveToken(accessToken);

    if (isNewUser) {
      // 닉네임 설정 화면으로 이동
      // 애플은 이름을 제공하지 않으므로 반드시 직접 입력받아야 함
      Navigator.pushNamed(context, '/set-nickname');
    } else {
      Navigator.pushNamed(context, '/home');
    }
  } else {
    // 에러 처리: data['detail']에 한국어 메시지 있음
    showErrorDialog(context, data['detail']);
  }
}
```

### 주의사항

**1. 이름은 최초 1회만 제공됩니다**

`credential.givenName`, `credential.familyName`은 최초 로그인 때만 값이 있고,  
이후엔 `null`입니다. 또한 이 값은 `id_token`에 포함되지 않아 백엔드에서 받을 수 없습니다.  
→ `is_new_user == true`일 때 앱 내에서 닉네임을 직접 입력받아야 합니다.

**2. 이메일 숨기기 옵션**

유저가 "이메일 가리기"를 선택하면 `@privaterelay.appleid.com` 형태의 릴레이 주소가 옵니다.  
백엔드에서 그대로 저장하므로 프론트에서 별도 처리는 불필요합니다.

**3. iOS 설정 선행 조건**

Apple Developer Console에서 해당 앱의 **Sign In with Apple capability**가 활성화되어 있어야 합니다.
