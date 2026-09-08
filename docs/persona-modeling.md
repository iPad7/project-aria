# 페르소나를 모델에 넣는 법 — 조사와 방침

**무엇을 다루나.** `persona_id`가 실제로 다른 말투·다른 인격이 되게 하는 **모델 쪽** 방법과, 그것이 aria의 페르소나 스키마에 요구하는 것. 조사 시점 **2026-09-08**.

**무엇을 다루지 않나.** 학습 파이프라인의 실행(데이터 생성 스크립트·SFT·DPO·레지스트리)은 **llmops 별도 repo**의 몫이다(GPU 하드 경계, `docs/architecture.md`). 여기 남는 것은 그 repo가 무엇을 목표로 삼아야 하는지와, 그 목표가 aria 코드에 요구하는 것이다.

---

## 문제 — 단일 도메인 SFT는 도메인을 가중치에 박는다

**실제로 겪은 일**(2025년경, 이 프로젝트 이전). 연애상담 QA 데이터셋을 만들어 인스트럭션 튜닝했더니, **일반 질문에도 연애상담 말투로 답하는** 모델이 나왔다. 규제가 과하게 걸린 것처럼 보였다.

이건 데이터 품질 문제가 아니라 **단일 도메인 SFT의 구조적 귀결**이다.

- Anthropic의 [Persona Vectors](https://arxiv.org/pdf/2507.21509)는 **좁은(narrow) 파인튜닝이 학습 목표와 무관한 축까지 모델을 움직인다**는 것을 활성 공간에서 측정해 보였다. 저자들은 이를 emergent misalignment라 부른다.
- 망각 쪽 수치도 나와 있다: continual fine-tuning에서 절대 능력 저하 **15~32%**, 하위 레이어 attention head의 **15~23%**가 심하게 교란된다([Mechanistic Analysis, 2026](https://arxiv.org/html/2601.18699v1)). 한 사례에서 초등 수학 29%를 풀던 모델이 새 태스크 8개를 배운 뒤 **2%**로 떨어졌다.

핵심은 이렇다. *연애상담 질문 → 연애상담 답변*으로 학습시키면 모델이 배우는 것은 **"나는 연애상담사다"**다. 도메인이 가중치에 들어가 버리므로 **프롬프트로 빼낼 수 없다.**

---

## 방침 — 학습 대상은 "도메인"이 아니라 "페르소나 추종"

그래서 이 프로젝트가 학습시킬 것은 *연애상담을 잘하는 모델*이 아니라 **시스템 프롬프트에 적힌 인격대로 답하는 모델**이다. 그러면 도메인이 가중치가 아니라 프롬프트에서 오고, 페르소나를 바꾸는 것이 곧 방송 주제를 바꾸는 것이 된다.

이 방침은 이미 코드가 가리키는 방향이기도 하다 — `chat/application/persona_prompt.py`가 하는 일이 페르소나를 시스템 메시지로 조립하는 것이고, `PersonaLLMPort` 뒤 멀티-LoRA 구상도 "베이스가 인격을 읽고, 개별 인격은 어댑터로 갈린다"를 전제한다.

---

## 데이터셋 — 질문은 일반 코퍼스에서, 답변만 페르소나로 다시 쓴다

[OpenCharacter](https://arxiv.org/html/2501.15427v1)가 이 방침의 구체적 레시피를 제시하고 실증했다.

**OpenCharacter-R (response rewriting)**: 기존 **일반** instruction 코퍼스의 **질문을 그대로 두고 답변만** 캐릭터 말투로 다시 쓴다.

- 소스 코퍼스: LIMA · Alpaca · PH-Instruct — **수학·QA·추론이 섞여 있다**
- Persona Hub의 대규모 페르소나에서 캐릭터 프로필 약 2만 개 합성, 대화당 캐릭터 3개를 랜덤 배정 → **30.6만 쌍**
- LLaMA-3 8B SFT → PersonaGym에서 **gpt-4o-2024-05-13·gpt-4o-mini를 상회**

**왜 이게 통하는가.** 학습 신호가 *수학 질문 → 그 인격의 말투로 된 수학 답변*이 되므로, 모델이 배우는 것이 "나는 X다"가 아니라 **"무슨 질문이든 X처럼 답한다"**가 된다. 도메인 지식은 원래 답변에서 보존되고 말투만 얹힌다.

**같은 질문 × 다른 페르소나 쌍이 반드시 있어야 한다.** 그것이 "페르소나를 읽는 것"을 가르치는 **유일한 신호**다. 한 질문에 한 인격만 대응되면 모델은 인격을 읽을 이유가 없다.

> **연애상담은 여러 갈래 중 하나로만 넣는다.** 첫 페르소나가 연애상담이더라도 데이터셋을 그쪽으로 채우면 위의 실패를 되풀이한다.

---

## 조향과 필터링 — 학습하지 않고도 인격을 움직인다 (2025~2026 신규)

[Persona Vectors](https://www.anthropic.com/research/persona-vectors)는 "사악함"·"아부"·"환각 경향" 같은 특질이 **활성 공간의 방향**으로 인코딩돼 있음을 보였다. 그 벡터에 계수를 곱해 특정 레이어의 hidden state에 더하면 그 특질 쪽으로 출력이 움직인다. 용도는 셋이다:

1. 배포 중 인격 드리프트 **모니터링**
2. 파인튜닝 중 **예방적 조향** — 학습이 인격을 틀지 못하게 잡는다
3. **학습 데이터 필터링** — 어떤 샘플이 인격을 틀 것인지 학습 **전에** 걸러낸다

**③이 이 프로젝트에 바로 쓰인다.** 데이터셋을 API로 합성할 때 이 필터를 통과시키면 "모델을 한 도메인 쪽으로 미는 샘플"을 학습 전에 뺄 수 있다. 2026년에는 SAE로 facet별 분리된 제어 벡터에 동적 라우팅을 붙이는 방향까지 나왔다.

---

## 드리프트 — 지금 구조는 강하지만, 히스토리를 넣기 시작하면 약해진다

방송은 몇 시간짜리 멀티턴이다. 2026 연구는 활성 공간에 **Assistant Axis**가 있고 긴 대화에서 모델이 그 축을 따라 **기본 어시스턴트로 되돌아간다**고 규명했다([persona drift](https://www.emergentmind.com/topics/persona-drift)). 완화법은 앵커 재삽입(주기적 페르소나 재천명), 턴마다 persona 임베딩 주입, 학습 시 랜덤 페르소나 믹스 등이다.

**aria는 지금 이 문제에 노출돼 있지 않다.** `persona_prompt.py`가 매 요청마다 시스템 프롬프트를 새로 조립하고 대화 히스토리를 프롬프트에 쌓지 않으므로, 드리프트할 긴 컨텍스트 자체가 없다.

> **단, #73으로 기록(`chat_message`)이 생겼다.** 앞으로 히스토리를 프롬프트에 넣어 맥락을 잇기 시작하면 이 문제가 그대로 들어온다. 그때 필요한 것이 앵커 재삽입이며, 넣기 **전에** 알고 있어야 뒤늦게 원인을 찾지 않는다.

---

## 평가 — 표준이 생겼고, 일반 능력 회귀를 함께 재야 한다

- **PersonaGym** — 페르소나 능력 종합(OpenCharacter가 쓴 것)
- **[RMTBench](https://arxiv.org/pdf/2507.20352)** — 멀티턴 user-centric 롤플레이
- **[SysBench 계열](https://arxiv.org/html/2608.19207)** — 시스템 메시지 준수. 정적 벤치마크에서 상호작용 서사(CharacterBox·AdaMARP) 쪽으로 이동 중

> **페르소나 능력 ≈ 지시 준수 능력.** *"Persona consistency is instruction following in costume"* — IFEval/IFBench 상위 모델이 캐릭터도 잘 지킨다([정리](https://benchlm.ai/best/roleplay)). **베이스 모델을 고를 때 IFEval을 본다.**

**평가에 일반 능력 회귀 측정을 반드시 넣는다.** 수학·QA 벤치를 학습 전후로 재는 것이, 위에서 겪은 실패를 **숫자로 잡는 유일한 장치**다. 페르소나 점수만 보면 그 실패가 그대로 통과한다.

---

## 어댑터 — LoRA, 과학습 주의

- **LoRA가 망각을 덜 겪는다**(베이스 지식 보존). full FT가 성능은 더 높다 — 이 프로젝트는 보존 쪽이 중요하므로 LoRA.
- **멀티-LoRA hot-swap**으로 한 베이스에 여러 인격을 서빙한다. `PersonaLLMPort` 뒤 구상 그대로이며, 앱은 여전히 `persona_id`만 넘긴다.
- 주의: [어댑터를 과하게 학습시키면 병합이 망가진다](https://arxiv.org/pdf/2506.14126).

---

## aria에 요구하는 것 — 버티컬 해제

위 데이터셋 설계("같은 질문 × 다른 페르소나")를 하려면 **방송 주제가 페르소나 속성이어야 한다.** 지금은 그렇지 않다:

```python
# chat/application/persona_prompt.py
DEFAULT_SYSTEM = "너는 연애 상담을 해 주는 AI 페르소나다. …"      # 폴백
out = [f"너는 '{profile.name}'이라는 이름의 AI 연애상담 스트리머다."]  # 프로필 있을 때
```

**두 번째 줄이 모든 페르소나 앞에 도메인을 못박는다.** 말투·가치관·나침반을 아무리 정성껏 넣어도 그 앞줄이 주제를 고정하므로, 주제가 다른 페르소나 쌍을 만들 수 없다.

해제하면 그 문장이 페르소나에서 나온다:

```python
out = [f"너는 '{profile.name}'이라는 이름의 AI 스트리머다."]
if profile.topic:
    out.append(f"방송 주제: {profile.topic}")
```

`communication_style`(#59)·`moral_compass`(#67)와 **같은 종류의 작업**이다 — 하드코딩돼 있던 인격의 한 축을 페르소나 데이터로 올리는 것. 이번 축은 "무엇을 하는 방송인가"이고, **데이터셋 설계의 선행 조건**이다.

---

## 요약 — llmops repo가 받아야 할 것

1. 베이스는 **IFEval 강한 모델**
2. 데이터셋은 **OpenCharacter-R 방식** — 일반 코퍼스의 질문 + 페르소나 말투로 다시 쓴 답변. 연애상담은 한 갈래로만
3. **같은 질문 × 다른 페르소나** 쌍을 반드시 포함
4. 학습 데이터에 **persona vector 필터**(선택)
5. 평가 = 페르소나 벤치 **+ 일반 능력 회귀**
6. **LoRA** 멀티 어댑터, 과학습 주의

## 참고

- [Persona Vectors: Monitoring and Controlling Character Traits in Language Models](https://arxiv.org/pdf/2507.21509) · [Anthropic 요약](https://www.anthropic.com/research/persona-vectors)
- [OpenCharacter: Training Customizable Role-Playing LLMs with Large-Scale Synthetic Personas](https://arxiv.org/html/2501.15427v1)
- [Mechanistic Analysis of Catastrophic Forgetting During Continual Fine-tuning (2026)](https://arxiv.org/html/2601.18699v1)
- [Persona Drift](https://www.emergentmind.com/topics/persona-drift) · [Persona Collapse](https://www.emergentmind.com/topics/persona-collapse)
- [RMTBench: Multi-Turn User-Centric Role-Playing](https://arxiv.org/pdf/2507.20352)
- [Benchmarking LLMs under System Messages (SysBench 계열)](https://arxiv.org/html/2608.19207)
- [From Memorization to Parameter Interference: How Overtraining Experts Harms Model Merging](https://arxiv.org/pdf/2506.14126)
- [Best LLMs for Roleplay — 벤치마크 정리](https://benchlm.ai/best/roleplay)
