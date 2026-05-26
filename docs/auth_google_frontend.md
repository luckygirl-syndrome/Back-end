# 구글 로그인 - 프론트엔드 연동 가이드 (Flutter)

## 백엔드 구현 방식 요약

**ID Token 검증 방식** 사용.

프론트에서 Google SDK로 로그인 → ID Token 발급 → 백엔드로 전달 → 백엔드가 검증 후 우리 서비스 JWT 발급

```
프론트: 구글 로그인 버튼 탭
  → google_sign_in 패키지가 ID Token 발급
  → 백엔드 POST /api/auth/google { id_token: "..." }
  → 백엔드: 토큰 검증 + 유저 조회/생성
  → 우리 서비스 JWT 반환
```

---

## 백엔드 엔드포인트

```
POST https://ttobaba.shop/api/auth/google
Content-Type: application/json
```

**요청**
```json
{
  "id_token": "구글_ID_Token"
}
```

**응답**
```json
{
  "status": "success",
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "is_new_user": true
}
```

- `is_new_user: true` → 첫 로그인 (온보딩 페이지로 이동)
- `is_new_user: false` → 기존 유저 (메인 페이지로 이동)

이후 모든 API 요청 시 헤더에 포함:
```
Authorization: Bearer <access_token>
```

---

## 프론트 구현 방법 (Flutter)

### 1. Google Client ID
```
672840096126-bkli6ul54r6mfua33g927fgn2mmajsgq.apps.googleusercontent.com
```

### 2. 패키지 추가 (`pubspec.yaml`)
```yaml
dependencies:
  google_sign_in: ^6.2.1
  http: ^1.2.0
```

### 3. Android 설정 (`android/app/build.gradle`)
```gradle
android {
    defaultConfig {
        minSdkVersion 21
    }
}
```

`android/app/src/main/res/values/strings.xml` 에 Client ID 추가:
```xml
<resources>
    <string name="default_web_client_id">672840096126-bkli6ul54r6mfua33g927fgn2mmajsgq.apps.googleusercontent.com</string>
</resources>
```

### 4. iOS 설정
`ios/Runner/Info.plist` 에 추가:
```xml
<key>GIDClientID</key>
<string>672840096126-bkli6ul54r6mfua33g927fgn2mmajsgq.apps.googleusercontent.com</string>
```

Google Cloud Console에서 iOS용 OAuth 클라이언트 ID도 따로 발급 필요 → `CFBundleURLSchemes` 에 추가.

### 5. 구글 로그인 코드

```dart
import 'package:google_sign_in/google_sign_in.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';

final GoogleSignIn _googleSignIn = GoogleSignIn(
  serverClientId: '672840096126-bkli6ul54r6mfua33g927fgn2mmajsgq.apps.googleusercontent.com',
);

Future<void> signInWithGoogle() async {
  try {
    final GoogleSignInAccount? account = await _googleSignIn.signIn();
    if (account == null) return; // 유저가 취소

    final GoogleSignInAuthentication auth = await account.authentication;
    final String? idToken = auth.idToken;

    if (idToken == null) throw Exception('ID Token을 가져올 수 없습니다.');

    // 백엔드로 ID Token 전달
    final response = await http.post(
      Uri.parse('https://ttobaba.shop/api/auth/google'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'id_token': idToken}),
    );

    final data = jsonDecode(response.body);

    if (data['status'] == 'success') {
      final String accessToken = data['access_token'];
      final bool isNewUser = data['is_new_user'];

      // 토큰 저장 (SharedPreferences 또는 secure storage 권장)
      // await storage.write(key: 'access_token', value: accessToken);

      if (isNewUser) {
        // 온보딩 페이지로 이동
      } else {
        // 메인 페이지로 이동
      }
    }
  } catch (e) {
    print('구글 로그인 실패: $e');
  }
}
```

---

## 에러 케이스

| 상황 | HTTP 상태 | 메시지 |
|------|-----------|--------|
| ID Token 유효하지 않음 | 401 | 유효하지 않은 구글 토큰입니다. |
| 이메일 없는 구글 계정 | 400 | 구글 계정에서 이메일을 가져올 수 없습니다. |
| 동일 이메일로 일반 가입된 계정 | 400 | 이미 이메일로 가입된 계정입니다. 일반 로그인을 이용해주세요. |

---

## Google Cloud Console 추가 설정 필요

Android, iOS 각각 OAuth 클라이언트 ID 발급 필요:
1. Google Cloud Console → API 및 서비스 → 사용자 인증 정보
2. **Android용** OAuth 클라이언트 ID 생성 → 패키지명 + SHA-1 인증서 지문 입력
3. **iOS용** OAuth 클라이언트 ID 생성 → 번들 ID 입력
