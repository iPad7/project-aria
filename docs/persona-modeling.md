# 페르소나를 모델에 넣는 법 — 조사와 방침

**무엇을 다루나.** `persona_id`가 실제로 다른 인격이 되게 하는 **모델 쪽** 방법과, 그것이 aria의 스키마에 요구하는 것. 조사 2026-09-08.

**무엇을 다루지 않나.** 학습 파이프라인의 실행(데이터 생성·SFT·DPO·레지스트리)은 **llmops 별도 repo**의 몫이다(GPU 하드 경계, `docs/architecture.md`). 여기 남는 것은 그 repo가 무엇을 목표로 삼아야 하는지와, 그 목표가 aria 코드에 요구하는 것이다.

> **정정 이력.** 이 문서의 1판(`4594d1a`)에는 원문 대조 없이 쓴 오류가 있었다. ① OpenCharacter의 대표 레시피를 R(response rewriting)이라고 적었으나 **최종 모델은 G**다. ② Assistant Axis의 드리프트 **방향을 반대로** 적었다. ③ 출처가 불안정한 preprint의 정밀 퍼센트를 근거로 썼다. ④ SysBench 링크가 그것을 인용한 다른 논문을 가리켰다. 2판은 아래 인용을 전부 원문으로 확인했다.

---

## 문제 — 단일 도메인 SFT는 도메인과 정체성을 함께 학습시킨다

**실제로 겪은 일**(이 프로젝트 이전). 연애상담 QA 데이터셋으로 인스트럭션 튜닝했더니 **일반 질문에도 연애상담 말투로 답하는** 모델이 나왔다.

두 가지가 이 현상을 설명한다.

- **일반 능력 저하는 잘 문서화돼 있다.** [More Than Catastrophic Forgetting (EMNLP 2024)](https://aclanthology.org/2024.emnlp-main.429/)은 *"performance on general tasks decreases after LLMs are fine-tuned on domain-specific tasks"*를 전제로 놓고, 단순 보존을 넘어 도메인 지식과 일반 능력을 **함께 쓰게 하는 것**(General Capabilities Integration)을 문제로 정의한다.
- **의도하지 않은 인격 변화도 관측된다.** [Persona Vectors](https://arxiv.org/pdf/2507.21509)는 좁은 파인튜닝이 **학습 목표와 무관한 행동 축까지** 모델을 움직일 수 있음을 활성 공간에서 보였다(evil·sycophancy·hallucination 경향 중심).

> **여기서 멈춰야 한다.** 위 두 결과는 *"모든 단일 도메인 SFT가 필연적으로 그 결과를 낳는다"*를 증명하지 않는다. 정확히 말할 수 있는 것은 이 정도다 — **단일 도메인 SFT는 도메인과 어시스턴트 정체성·말투를 얽어 학습시키기 쉽고, 좁은 파인튜닝이 의도하지 않은 행동 변화까지 유발할 수 있다는 최근 결과가 있다.** 그래서 aria는 도메인 특화 모델을 만들지 않는다.

---

## 방침 — 학습 대상은 "도메인"이 아니라 "페르소나 조건 추종"

**aria는 domain-specialized 모델을 만들지 않는다.** 학습시킬 능력은 **"시스템 프롬프트로 주어진 페르소나를 읽고, 도메인에 관계없이 그 페르소나에 맞게 응답하는 능력"**이다.

그러면 도메인이 가중치가 아니라 조건(conditioning)에서 오고, 페르소나·주제를 바꾸는 것이 배포가 아니라 데이터 변경이 된다. 이 방침은 코드가 이미 가리키는 방향이기도 하다 — `chat/application/persona_prompt.py`가 하는 일이 인격을 시스템 메시지로 조립하는 것이다.

---

## 데이터셋 — 기본은 생성(G), 다양한 instruction에 페르소나를 교차 배정

[OpenCharacter](https://arxiv.org/html/2501.15427v1)가 이 방침의 검증된 레시피다. **최종 모델은 세 instruction 코퍼스(PH-Instruct·LIMA·Alpaca)를 합치고, LLaMA-3-70B-Instruct가 페르소나와 instruction을 보고 답을 새로 생성하는 G 방식**을 쓴다(Table 2). LLaMA-3-8B SFT로 PersonaGym에서 gpt-4o-2024-05-13·gpt-4o-mini를 상회했다.

**G와 R의 차이는 실측돼 있다** (PersonaGym-Light PScore-L):

| 합성 방식 | PScore-L |
|---|---|
| R (rewriting) / GPT-4o | 4.35 |
| R / LLaMA-3-70B | 4.37 |
| G (generation) / GPT-4o | 4.60 |
| **G / LLaMA-3-70B (최종)** | **4.66** |

저자 표현 그대로 *"OpenCharacter-G significantly outperforms OpenCharacter-R in all scenarios."*

- **R이 지는 이유**: LIMA·Alpaca의 원본 답변 품질이 낮아, 그것을 다시 쓰면 **학습 답변이 한 번 더 열화된다.**
- **PH-Instruct에는 R을 쓸 수 없다**: 원본 답변이 공개돼 있지 않다(Table 1, *"responses are not released"*).
- **R이 유용한 곳은 따로 있다**: 소설·게임처럼 **원본 지식을 엄격히 지켜야 하는** 경우. 사실을 바꾸면 안 되는 task에서 환각을 막는다.

> **그래서 aria의 방침.** 기본 합성은 **G**를 쓴다. 사실 보존이 필요한 갈래(정보성 질의 등)에서만 **R 또는 제약 있는 rewriting**을 선택적으로 병행한다.

**교차 배정이 핵심이다.** OpenCharacter는 dialogue session마다 **캐릭터 3개를 무작위 배정**한다 — instruction 102,084개 × 3 ≈ **306k dialogue**. 같은 질문 `x`에 대해 `(p₁,x)→y₁`, `(p₂,x)→y₂`, `(p₃,x)→y₃`를 보여주면 질문은 고정되고 페르소나만 바뀌므로, 출력 차이를 설명하는 가장 자연스러운 방법이 **페르소나 조건을 읽는 것**이 된다.

> 이것이 **가장 깨끗한 disentanglement 신호 중 하나**다. 유일한 신호는 아니다.

**연애상담은 여러 갈래 중 하나로만 넣는다.** 첫 페르소나가 연애상담이더라도 데이터셋을 그쪽으로 채우면 위의 실패를 되풀이한다.

---

## 세 축을 분리한다 — persona × 방송 맥락 × instruction

목표가 `domain ≠ persona`인데, 페르소나 A가 늘 연애상담만 하면 **데이터에 `A ↔ 연애상담` 상관이 다시 생긴다.** 도메인을 가중치에서 빼내려다 페르소나를 통해 도로 집어넣는 셈이다.

그래서 조건 변수를 셋으로 나눈다:

```
Persona A × 연애상담 × 질문 X      Persona B × 연애상담 × 질문 X
Persona A × 게임     × 질문 Y      Persona C × 연애상담 × 질문 X
Persona A × 잡담     × 질문 Z
```

- **누구인가 / 어떻게 말하는가** → 페르소나 (말투·가치관·나침반)
- **오늘 무엇을 하는가** → 방송 맥락
- **무엇을 물었는가** → 시청자 메시지

학습 데이터에서 셋을 가능한 한 교차시켜 얽힘을 억제한다.

---

## 드리프트 — 방향은 "어시스턴트에서 멀어지는" 쪽이다

[The Assistant Axis](https://www.anthropic.com/research/assistant-axis)는 페르소나 간 변이를 가장 많이 설명하는 방향을 찾았고, 긴 대화에서 모델이 **Assistant에서 멀어지는** 것을 관측했다. 트리거는 셋이다: **감정적 취약성 노출**, 모델 자신의 제약에 대한 **메타 성찰 요구**, 특정 **목소리·어조 요구**. 코딩 대화는 오히려 Assistant에 붙들어 둔다.

> **이건 우리에게 남 일이 아니다.** 연애상담 방송은 논문이 지목한 **감정적 취약성 대화** 그 자체다. 즉 경계해야 할 것은 "페르소나가 밋밋해지는 것"이 아니라 **의도치 않은 다른 쪽으로 흘러가는 것**이다.
>
> 다만 이 논문은 **"커스텀 시스템 프롬프트 페르소나가 기본 어시스턴트로 붕괴하는가"를 다루지 않는다.** 자연스러운 대화에서의 유기적 드리프트를 본 것이다. 그 이상으로 끌어다 쓰지 않는다.

**aria의 현재 구조는 이미 앵커 재삽입에 해당한다.** `persona_prompt.py`가 매 요청마다 시스템 프롬프트를 새로 조립한다. #73으로 기록이 생겨 앞으로 히스토리를 프롬프트에 넣더라도 `system persona → history → current user` 형태라면, **페르소나 프롬프트가 맨 앞에 한 번만 있었던 긴 시퀀스와는 조건이 다르다.**

그러므로 결론은 "히스토리를 넣는 순간 문제가 생긴다"가 아니라 **"넣을 때 멀티턴 페르소나 회귀 테스트를 함께 넣는다"**이다.

---

## 평가 — 세 축을 함께 본다

- **PersonaGym** — 페르소나 능력 종합 (OpenCharacter가 쓴 지표)
- **[RMTBench](https://arxiv.org/pdf/2507.20352)** — 멀티턴 user-centric 롤플레이
- **[SysBench (ICLR 2025)](https://proceedings.iclr.cc/paper_files/paper/2025/hash/b917f916e7eed84ffe8f5e63492b2be8-Abstract-Conference.html)** — 시스템 메시지 준수. 수작업 시스템 메시지 500개 + 멀티턴 대화로 **제약 위반 · 지시 오판 · 멀티턴 불안정성** 셋을 본다. aria와 가장 직접적으로 관련 있다.

**베이스 모델은 세 축으로 고른다:**

```
지시 준수(IFEval·SysBench) + 페르소나/롤플레이 + 일반 능력
```

> IFEval은 **필요조건에 가까운 신호이지 페르소나 능력의 대리 지표가 아니다.** IFEval이 검증하는 것은 주로 키워드·길이·형식처럼 **자동 검증 가능한 제약**이고, 말투나 멀티턴 안정성까지 보장하지 않는다. *"Persona consistency is instruction following in costume"*은 [BenchLM](https://benchlm.ai/best/roleplay)의 편집 휴리스틱이지 논문 결론이 아니다 — 전용 롤플레이 벤치가 없어 IFEval·WildBench·MuSR을 조합해 간접 랭킹한다고 그쪽도 명시하고 있다.

**학습 전후로 일반 능력 회귀를 반드시 측정한다.** 페르소나 점수만 보면 위에서 겪은 실패가 그대로 통과한다.

---

## 어댑터 — 범용 페르소나 추종이 먼저, 개별 인격은 그 위에

OpenCharacter가 흥미로운 이유는 **2만 캐릭터로 학습한 단일 모델이 추론 시 처음 보는 프로필도 수행하는 character generalization**이다. **캐릭터마다 어댑터가 필요한 구조가 아니다.**

그래서 2단으로 둔다:

```
베이스 Instruct 모델
  + 범용 페르소나 추종 LoRA        ← baseline
  + 페르소나 프로필 프롬프트
  ( + 개별 페르소나 LoRA )          ← 프로필만으로 fidelity가 부족할 때만
```

> **`docs/architecture.md`의 "운영 = vLLM(멀티-LoRA)"와 충돌하지 않는다.** hot-swap 인프라는 그대로 쓰되 **어댑터의 용도가 바뀐다** — "인격마다 하나"가 아니라 "페르소나 추종 하나 + 필요 시 개별". 멀티-LoRA는 **확장 수단이지 페르소나 아키텍처의 전제가 아니다.**

LoRA를 먼저 쓰는 이유는 일반 능력 보존과 배포 편의다. 다만 **LoRA가 망각에서 자유로운 것은 아니다** — 줄이는 경향이 있을 뿐이다.

> 별개 주의: [여러 전문가 모델을 **병합**할 때는 과학습이 병합 품질을 떨어뜨린다](https://arxiv.org/pdf/2506.14126)(full FT·LoRA 양쪽에서 관측). **hot-swap만 한다면 직접적인 위험은 아니다.**

---

## Persona Vector 기반 데이터 감사 — 후속 실험 트랙

Persona Vectors는 활성 공간의 방향으로 특질을 **모니터링·조향**하고, 학습 **전에** 문제 샘플을 걸러낼 수 있음을 보였다(LMSYS-Chat-1M에서 검증).

**다만 baseline 요구사항으로 넣기엔 이르다.**

- 검증된 대상은 주로 **행동 특질**(sycophancy·evil·hallucination 경향)이다. `연애상담사 ↔ 범용 어시스턴트` 같은 **도메인/페르소나 얽힘 탐지기**로 쓸 수 있을지는 **우리가 검증해야 할 가설**이다.
- **대상 모델의 hidden activation 접근이 필요하다.** OpenAI API로 데이터를 합성하는 단계에서는 애초에 쓸 수 없고, open-weight 모델을 손에 쥔 뒤의 이야기다.

→ llmops에서는 `optional / experimental: persona-vector 기반 데이터셋 스크리닝`으로 둔다.

---

## aria에 요구하는 것 — 하드코딩 제거와 방송 맥락의 자리

현재 모든 페르소나 앞에 도메인이 못박혀 있다:

```python
# chat/application/persona_prompt.py
DEFAULT_SYSTEM = "너는 연애 상담을 해 주는 AI 페르소나다. …"       # 폴백
out = [f"너는 '{profile.name}'이라는 이름의 AI 연애상담 스트리머다."]  # 프로필 있을 때
```

**방송 주제는 `Persona`가 아니라 `Room`에 둔다.** 방(`chat_room`)이 곧 방송 세션이고 이미 `name`·`description`·상태를 갖고 있다 — 새 엔티티가 필요 없다. 페르소나에 두면 위에서 억제하려던 `페르소나 ↔ 도메인` 상관을 스키마가 되살린다.

**`Persona.default_topic`은 두지 않는다.** 제품상 페르소나마다 주력 콘텐츠가 있는 것이 자연스럽지만, 그 순간 같은 상관이 약한 형태로 되살아난다. 필요해지면 붙이는 것이 붙였다 떼는 것보다 쉽다.

시스템 프롬프트는 이렇게 갈린다:

```text
너는 '{persona.name}'이라는 이름의 AI 스트리머다.
[인격]  말투 · 가치관 · 나침반 …
[이번 방송]  주제: {room.topic}
```

**따라오는 설계 결과 하나.** 생성 워커는 지금 `GenerationRequest`(room_id·persona_id·source·prompt)만 받고 **방을 읽지 않는다.** 주제를 프롬프트에 넣으려면 워커가 방을 읽어야 한다 — **페이로드에 실으면 안 된다.** 큐에 남아 있던 옛 요청이 옛 주제로 답하는, `persona_prompt.py`가 이미 문서화한 그 함정이다(프로필을 소비 시점에 읽는 이유와 같다). 워커에 DB 세션은 이미 있으므로 방 리포지토리 배선이 하나 늘어난다.

---

## 요약 — llmops repo가 받아야 할 것

1. **도메인 특화 모델을 만들지 않는다.** 학습 대상은 시스템 프롬프트의 페르소나를 읽고 도메인 무관하게 그에 맞춰 답하는 능력
2. 코퍼스는 **broad-domain instruction**, 동일 instruction에 **여러 페르소나를 교차 배정**
3. 합성은 **G(페르소나 조건 응답 생성)가 기본**, 사실 보존이 필요한 갈래에만 제약 rewriting 병행
4. **페르소나와 방송 주제를 별도 조건 변수로** 취급하고 가능한 한 교차
5. 평가는 **지시 준수 + 페르소나 + 일반 능력 회귀**를 함께
6. **LoRA 우선**(보존·배포 편의), 다만 망각에서 자유롭지 않음. 개별 페르소나 어댑터는 확장 수단
7. Persona Vector/SAE 조향은 **후속 실험 트랙**(데이터 감사·모니터링·추론 조향)

## 참고

- [OpenCharacter: Training Customizable Role-Playing LLMs with Large-Scale Synthetic Personas](https://arxiv.org/html/2501.15427v1) — 최종 레시피는 G, ablation Table 2·3
- [Persona Vectors: Monitoring and Controlling Character Traits in Language Models](https://arxiv.org/pdf/2507.21509) · [Anthropic 요약](https://www.anthropic.com/research/persona-vectors)
- [The Assistant Axis](https://www.anthropic.com/research/assistant-axis) — 드리프트 방향과 트리거
- [More Than Catastrophic Forgetting: Integrating General Capabilities For Domain-Specific LLMs (EMNLP 2024)](https://aclanthology.org/2024.emnlp-main.429/)
- [SysBench: Can LLMs Follow System Message? (ICLR 2025)](https://proceedings.iclr.cc/paper_files/paper/2025/hash/b917f916e7eed84ffe8f5e63492b2be8-Abstract-Conference.html)
- [RMTBench: Multi-Turn User-Centric Role-Playing](https://arxiv.org/pdf/2507.20352)
- [From Memorization to Parameter Interference: How Overtraining Experts Harms Model Merging](https://arxiv.org/pdf/2506.14126) — **병합**에 대한 결과
- [BenchLM — Roleplay 랭킹](https://benchlm.ai/best/roleplay) (편집 휴리스틱, 논문 아님)
