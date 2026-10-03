# Subscription model selection and reasoning

Provider documentation checked on 2026-10-02. Account catalogs determine model
availability; a public model announcement does not establish subscription access.
Refresh available models after a provider/client update. Unknown capabilities keep
the provider default instead of sending invented effort values.

The dashboard saves reasoning separately for each connected account/model when
the user presses **Use this account and model**. Changing the slider is a pending
preference until that button is pressed. It changes inference only: no simulation,
force field, chemistry or preparation setting is applied. Requests pin the chosen
model and reasoning; changing either invalidates an in-flight response.

| Provider | Model catalog | Actual request control |
| --- | --- | --- |
| ChatGPT/Codex | Public subscription catalog plus explicitly verified GPT-6 access | Responses `reasoning.effort` |
| Claude | Official client's tool-free initialize response, without an inference turn | Explicit `--model` and `--effort`, validated against native model metadata |
| Kimi | Managed Kimi Code subscription model catalog | Validated `KIMI_MODEL_THINKING_EFFORT`, or documented thinking toggle when supported |
| Gemini | Connected account's Code Assist quota model catalog | GenerateContent `thinkingConfig.thinkingLevel` for supported Gemini 3 models; `thinkingBudget` for Gemini 2.5 |

Codex order: GPT-6 Astra, GPT-6.1 Sol, GPT-6 Sol, GPT-6 Luna, then the
remaining catalog models in their provider order. Astra/Sol 6.1 accept low through
max; Sol 6/Luna 6 and GPT-5.6 also accept none. GPT-5.5 ends at xhigh. No ultra
value is sent to Responses. New capabilities require a documentation update.

Claude model metadata supplies the offered levels, including account/client
limits. Fable/best and usage-credit models are excluded because non-interactive
Claude requests can bill usage credits without presenting the normal consent
prompt. Updating the official Claude client makes newer catalog entries visible;
this app does not assume older aliases resolve to the latest announced version.
Kimi models with always-on thinking and no declared adjustable effort retain
their provider default. Gemini 2.5 Pro cannot disable thinking; its budget starts
at 128 tokens. Flash/Flash Lite offer 0 to disable and documented budget steps.
Budgets are discrete presets, not a guarantee of exact token use.

## Primary references

- [OpenAI subscription models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- OpenAI model cards: [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol 6.1](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [Sol 6](https://developers.openai.com/api/docs/models/gpt-6-sol), [Luna 6](https://developers.openai.com/api/docs/models/gpt-6-luna), [Sol 5.6](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [Terra 5.6](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [Luna 5.6](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5)
- [Claude model configuration, effort and usage credits](https://code.claude.com/docs/en/model-config)
- [Official Claude SDK initialization protocol](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py)
- [Kimi model configuration](https://moonshotai.github.io/kimi-code/en/configuration/config-files.html) and [environment controls](https://moonshotai.github.io/kimi-code/en/configuration/env-vars.html)
- [Gemini GenerateContent thinking controls](https://ai.google.dev/gemini-api/docs/generate-content/thinking)

## Verification scope

Transport tests verify the chosen controls reach tool-free requests, unsupported
levels fail, preferences persist by model, and changed preferences reject stale
responses. Claude initialization was exercised with the installed official client
without credentials or inference. Gemini's installed native content generator was
tested against an intercepted fixture transport. Kimi tool isolation was tested
with its installed client against a local fixture model. Only OpenAI currently has
a completed live subscription sign-in on this dashboard; other account entitlements
and live inference still require each user's own browser sign-in.

Acceptance on 2026-10-02: 76 provider/reasoning tests passed, followed by the
updated 16-test Kimi suite proving `high` reached the real native client's
intercepted tool-free request. The live dashboard preserved low effort after
reload, changed the offered levels when switching models, restored the saved
per-model value, and completed a GPT-6 Luna subscription reply at low effort.
The user's original provider-default preference was restored after verification.
