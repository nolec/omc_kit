# 공통 디자인 근거
Figma section 5437:49209 광고 구간 유지보수.
메모 5437:49192: 보더 추가, 패널 쉐도우 추가 (다른 광고 구간에도 모두 동일하게 적용).
메모 5437:49202: 보더 추가. 예외로 들어간 컴포넌트의 화살표 아이콘 제거, 일정 시간 지나면 자동으로 스와이프.
하위 카드 5437:48699: width 340px, height 200px, background white, overflow clip, border radius 12px, border 1px solid rgba(0,0,0,0.1), shadow 0px 1px 2px 0px rgba(0,0,0,0.05). 실제 광고 소재는 동적 공급이므로 Figma 예시 이미지를 코드에 고정하지 않는다.
검증된 기존 토큰 BOX_SHADOW.HOME_CARD = 0px 1px 2px 0px rgba(0,0,0,0.05). 최종 구현은 각 도구가 기존 코드에서 직접 조사한다.
