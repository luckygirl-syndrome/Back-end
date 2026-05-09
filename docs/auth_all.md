# 로그인 기능 전체 정리

## 공통 사항

### JWT 토큰 사용법
모든 로그인 방식은 동일하게 우리 서비스 JWT를 발급해요.

인증이 필요한 API 요청 시 헤더에 포함:
```
Authorization: Bearer <access_token>
```

### JWT 구조
- `sub`: `user_id` (DB 기본키, 모든 로그인 방식 통일)
- 만료: 24시간

---

## 1. 일반 로그인

### 회원가입
```
POST https://ttobaba.shop/api/auth/signup
```
```json
// 요청
{
  "email": "user@example.com",
  "password": "최소8자",
  "nickname": "닉네임"
}

// 응답
{
  "status": "success",
  "user_id": 1,
  "email": "user@example.com",
  "nickname": "닉네임"
}
```

### 로그인
```
POST https://ttobaba.shop/api/auth/login
```
```json
// 요청
{
  "email": "user@example.com",
  "password": "비밀번호"
}

// 응답
{
  "status": "success",
  "access_token": "eyJhbGci...",
  "token_type": "bearer"
}
```

**에러 케이스**
| 상황 | 상태 코드 | 메시지 |
|------|-----------|--------|
| 이메일 없음 / 비밀번호 틀림 | 401 | 로그인 정보가 올바르지 않습니다. |
| 비밀번호 8자 미만 (회원가입) | 422 | 비밀번호는 최소 8자 이상이어야 합니다. |
| 이미 가입된 이메일 | 400 | 이미 존재하는 이메일입니다. |

---

## 2. 구글 로그인

```
POST https://ttobaba.shop/api/auth/google
```
```json
// 요청
{
  "id_token": "구글_ID_Token"
}

// 응답
{
  "status": "success",
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "is_new_user": true
}
```

- `is_new_user: true` → 첫 로그인, 온보딩으로 이동
- `is_new_user: false` → 기존 유저, 메인으로 이동

**에러 케이스**
| 상황 | 상태 코드 | 메시지 |
|------|-----------|--------|
| 유효하지 않은 토큰 | 401 | 유효하지 않은 구글 토큰입니다. |
| 동일 이메일로 일반 가입된 계정 | 400 | 이미 이메일로 가입된 계정입니다. 일반 로그인을 이용해주세요. |

### Flutter 구현
```dart
import 'package:google_sign_in/google_sign_in.dart';

final GoogleSignIn _googleSignIn = GoogleSignIn(
  serverClientId: '672840096126-bkli6ul54r6mfua33g927fgn2mmajsgq.apps.googleusercontent.com',
);

Future<void> signInWithGoogle() async {
  final account = await _googleSignIn.signIn();
  if (account == null) return;

  final auth = await account.authentication;
  final idToken = auth.idToken;

  final response = await http.post(
    Uri.parse('https://ttobaba.shop/api/auth/google'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({'id_token': idToken}),
  );

  final data = jsonDecode(response.body);
  // data['access_token'] 저장
  // data['is_new_user'] 로 온보딩 분기
}
```

---

## 3. 카카오 로그인

```
POST https://ttobaba.shop/api/auth/kakao
```
```json
// 요청
{
  "id_token": "카카오_ID_Token"
}

// 응답
{
  "status": "success",
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "is_new_user": true
}
```

> **주의**: 카카오는 이메일을 제공하지 않아요 (비즈앱 심사 필요). 유저 식별은 카카오 고유 ID(`sub`)로 해요.

**에러 케이스**
| 상황 | 상태 코드 | 메시지 |
|------|-----------|--------|
| 유효하지 않은 토큰 | 401 | 유효하지 않은 카카오 토큰입니다. |
| 토큰에서 유저 정보 없음 | 400 | 카카오 토큰에서 유저 정보를 가져올 수 없습니다. |

### Flutter 구현
```dart
import 'package:kakao_flutter_sdk_user/kakao_flutter_sdk_user.dart';

Future<void> signInWithKakao() async {
  // 카카오톡 설치 여부에 따라 분기
  if (await isKakaoTalkInstalled()) {
    await UserApi.instance.loginWithKakaoTalk();
  } else {
    await UserApi.instance.loginWithKakaoAccount();
  }

  // ID Token 가져오기 (OIDC 활성화 필요)
  final tokenInfo = await TokenManagerProvider.instance.manager.getToken();
  final idToken = tokenInfo?.idToken;

  final response = await http.post(
    Uri.parse('https://ttobaba.shop/api/auth/kakao'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({'id_token': idToken}),
  );

  final data = jsonDecode(response.body);
  // data['access_token'] 저장
  // data['is_new_user'] 로 온보딩 분기
}
```

**pubspec.yaml**
```yaml
dependencies:
  kakao_flutter_sdk_user: ^1.9.0
```

---

## 4. 카카오 계정 연결

이미 이메일/구글로 가입한 유저가 카카오 계정을 연결할 때 사용해요.

```
POST https://ttobaba.shop/api/auth/kakao/connect
Authorization: Bearer <access_token>
```
```json
// 요청
{
  "id_token": "카카오_ID_Token"
}

// 응답
{
  "status": "success",
  "message": "카카오 계정이 연결되었습니다."
}
```

연결 후에는 카카오 로그인으로도 같은 계정에 로그인돼요.

**에러 케이스**
| 상황 | 상태 코드 | 메시지 |
|------|-----------|--------|
| 이미 카카오 연결된 계정 | 400 | 이미 카카오 계정이 연결되어 있습니다. |
| 다른 계정에 이미 연결된 카카오 | 400 | 이미 다른 계정에 연결된 카카오 계정입니다. |

---

## 로그인 방식별 계정 분리 정책

| 가입 방식 | 이메일 저장 | 카카오 연결 가능 |
|-----------|------------|----------------|
| 일반 가입 | O | O (`/auth/kakao/connect`) |
| 구글 로그인 | O | O (`/auth/kakao/connect`) |
| 카카오 로그인 | X | - |

---

## 백엔드 개발자 참고

- JWT `sub` = `user_id` (문자열)
- 카카오 유저는 `email=null`, `social_provider="kakao"`, `social_id=카카오sub`
- 구글 유저는 `email` 있음, `social_provider="google"`, `social_id=구글sub`
- 일반 유저는 `social_provider=null`, `password=bcrypt해시`
- 관련 파일: `app/users/router.py`, `app/core/security.py`, `app/users/schemas.py`
