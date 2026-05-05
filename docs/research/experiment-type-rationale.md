# Experiment-Type Dimension-Weight Rationale

This document records the authoritative rationale for every non-default dimension weight
assigned to the seven new experiment types introduced in this project:
`evidence_synthesis`, `observational_correlational`, `instrument_validation`,
`simulation_modeling`, `factorial_design`, `single_subject`, and
`qualitative_interpretive`.

**Scope.** Only non-default weights are documented here. The dimension
`resource_proportionality` defaults to `L` for every type and is not discussed
further. Weights of `M` (medium) are the implicit baseline for most dimensions; any
departure to `H` (high), `L` (low), or `S` (silent) is explained below.

**Citation removal note.** An earlier draft of this material contained synthetic
deep-research citation markers (left-double-angle-bracket + line-reference fragments).
All such markers have been removed. Every verifiable claim now cites a source from the
Reference List at the end of this document; claims that cannot be traced to a specific
published passage use appropriately hedged language ("may require", "is often
recommended", "typically involves").

---

## 1. `evidence_synthesis`

Evidence synthesis — systematic reviews and meta-analyses — aggregates effect estimates
from a heterogeneous body of primary studies. The design does not generate new
observations directly; it reanalyses existing data. This derivation character shapes
nearly every dimension weight.

### `variance_protocol: H`

Heterogeneity quantification is a first-order concern in any meta-analysis.
Borenstein, Hedges, Higgins, and Rothstein [B1] devote substantial treatment to the
choice between fixed-effect and random-effects models, showing that the between-study
variance component (tau-squared) must be estimated and reported regardless of which
model is chosen. The *Cochrane Handbook* [C1] §9.5 similarly requires heterogeneity
statistics (I-squared, Q-statistic) in every forest plot. Because the analyst has no
direct control over primary-study design, all variance-reduction levers — sampling
decisions, blocking, stratification — must be handled at the protocol level through
inclusion/exclusion criteria and subgroup definitions. This makes the variance-protocol
dimension substantially more demanding than in primary studies where randomisation
handles most confounding directly.

### `statistical_corrections: H`

Systematic reviews commonly test multiple subgroup hypotheses, moderators, and
sensitivity analyses in a single synthesis. The *Cochrane Handbook* [C1] §16.7.2 notes
that multiplicity across subgroup comparisons inflates the Type I error rate and
recommends pre-registering a small number of a-priori subgroups and treating post-hoc
analyses as exploratory. Borenstein et al. [B1] further discuss publication-bias
corrections (trim-and-fill, Egger's test, PET-PEESE) that constitute an additional
layer of statistical adjustment. The combination of heterogeneity modelling, subgroup
testing, and publication-bias assessment makes the statistical-corrections burden
substantially higher than in a single, pre-registered primary study.

### `ecological_validity: L`

A meta-analysis does not generate primary data; its ecological fidelity is derived
entirely from the studies it aggregates. If the included studies themselves were
conducted in naturalistic settings the synthesis may inherit high ecological validity,
but the synthesis design has no direct mechanism to ensure this. Rating ecological
validity as a weight-worthy concern at the synthesis level is generally not meaningful
— reviewers can note effect-size heterogeneity across applied versus laboratory studies,
but they cannot redesign the primary studies. An `L` weight reflects this structural
limitation.

### `causal_structure: M`

Random-effects meta-analysis can support causal inference when the included studies are
randomised controlled trials, but the inference remains contingent on the quality of
randomisation in primary studies [B1], [C1]. When the synthesis includes both
experimental and observational studies, causal claims require careful qualification.
The weight is retained at `M` (medium) rather than elevated to `H` because the causal
machinery belongs to the primary studies, not the synthesis design itself, and because
many syntheses are explicitly descriptive rather than causal.

---

## 2. `observational_correlational`

Observational studies — cross-sectional surveys, cohort studies, case-control designs
— observe naturally occurring variation without experimental manipulation. Their
distinctive challenge is separating association from causation in the absence of
randomisation.

### `causal_structure: L`

Without randomisation or an equivalent quasi-experimental device, observed associations
cannot be given unambiguous causal interpretations. Rosenbaum [R1] provides a
systematic treatment of sensitivity analysis — the tools used to ask how strong an
unmeasured confounder would have to be to explain away a detected association — and
concludes that such analyses are necessary precisely because the observational setting
cannot rule out confounding by design. Shadish, Cook, and Campbell [S1] Chapter 1
similarly distinguish between descriptive, predictive, and causal purposes, noting that
observational designs are strongest for the first two. A low weight on causal structure
reflects not that causality is irrelevant but that the design affords limited leverage
over it.

### `variance_protocol: H`

In the absence of randomisation, confounder selection and adjustment strategy become
the primary design decisions. Rosenbaum [R1] Chapter 3 argues that the choice of
matched or stratified design — which covariates to include, which balance metric to
optimise — is the observational analogue of randomisation and demands equally careful
prespecification. Failing to document the variance protocol makes replication and
sensitivity analysis impossible. The high weight reflects that observational analysts
effectively substitute design-time variance decisions for the automated balance that
randomisation provides.

### `ecological_validity: H`

Observational studies are conducted in real-world settings, which is precisely their
comparative advantage over laboratory experiments. Shadish et al. [S1] §3.4 discuss
external validity at length, noting that observational cohort studies and surveys
typically recruit from broader and more representative populations than convenience
samples used in laboratory research. When the research question concerns naturally
occurring behaviour, ecological validity is a deliberate strength of the design, not an
incidental feature, warranting a high weight.

### `measurement_alignment: M`

When constructs are measured in naturalistic settings, instrument performance may differ
from the psychometric conditions under which the instrument was developed. Kline [K1]
Chapter 2 discusses reliability generalisation — the observation that a coefficient
alpha obtained in a standardisation sample need not transfer to a new population or
context. Measurement alignment remains a medium priority rather than high because the
observational analyst typically cannot redesign the instrument mid-study; the concern is
real but bounded in scope compared to an instrument-validation study where measurement
is the primary object.

---

## 3. `instrument_validation`

Instrument-validation studies exist to characterise the psychometric properties of a
measure: reliability, factorial structure, convergent and discriminant validity, and
population-specific functioning (differential item functioning, DIF).

### `measurement_alignment: H`

Measurement is the design's primary object. Kline [K1] provides comprehensive treatment
of reliability coefficients (Cronbach's alpha, test-retest, parallel forms), exploratory
and confirmatory factor analysis, and the multi-trait multi-method matrix as a framework
for construct validity. Every design decision in an instrument-validation study — sample
size, calibration versus holdout split, anchor item selection — is motivated by the goal
of characterising measurement properties. No other dimension approaches this centrality,
which justifies the high weight.

### `causal_structure: L`

Instrument validation is fundamentally correlational in nature. The goal is to show
that items hang together (internal consistency), that the scale correlates with related
constructs (convergent validity), and that it does not correlate with unrelated
constructs (discriminant validity). None of these goals require or support causal
claims. Attributing a causal direction to an item-factor relationship would be a
category error in standard psychometric theory [K1] Chapter 5.

### `ecological_validity: M`

A validation study must demonstrate that the instrument functions adequately in the
population for which it is intended. Kline [K1] Chapter 11 discusses DIF analysis and
population portability: a scale validated on a university convenience sample may behave
differently in clinical or community samples. Ecological validity is therefore relevant
at a medium level — the analyst must show generalisability beyond the calibration
sample — but it is subordinate to measurement alignment.

### `statistical_corrections: H`

Instrument validation typically involves iterative model fitting: initial exploratory
factor analysis guides item selection, which informs a confirmatory factor analysis on
a separate sample. Kline [K1] §8.6 warns explicitly about capitalising on chance when
item selection is data-driven, recommending split-sample or cross-validation strategies.
DIF analysis across subgroups adds further multiplicity. The statistical corrections
burden is high because the same dataset is often interrogated for reliability,
factorial validity, and convergent validity simultaneously.

---

## 4. `simulation_modeling`

Simulation studies — discrete-event, Monte Carlo, agent-based — generate synthetic data
from a formal model of a system. They are used to study systems that are too costly,
too dangerous, or too slow to observe directly.

### `variance_protocol: H`

Simulation output is stochastic, and the analyst controls the variance entirely through
experimental design choices: number of replications, common random number streams,
antithetic variates, and control variates. Law and Kelton [L1] Chapter 11 provide
detailed guidance on replication-and-deletion warm-up analysis and on variance-reduction
techniques. Without a careful variance protocol, confidence intervals on simulation
output metrics may be unreliable regardless of how faithful the model is to the real
system. The high weight reflects that variance management is an intrinsic part of the
simulation design, not an afterthought.

### `ecological_validity: L`

A simulation model is an approximation. Law and Kelton [L1] §5.4 discuss model
validation — the process of determining whether a model is an accurate enough
representation for its intended purpose — and note that no model is valid in an absolute
sense. The degree to which simulation results generalise to the real system depends on
how thoroughly the model has been validated and on the assumptions embedded in its
structure. Because ecological validity is structurally bounded by the approximation
inherent to any model, it warrants a low weight relative to internal (within-model)
validity.

### `causal_structure: M`

Within a well-specified simulation model, causal statements are valid by construction:
the analyst controls all mechanisms and can apply counterfactual interventions cleanly.
However, the real-world causal interpretation of those results is conditional on model
validity [L1]. Because causal claims are unambiguous within the model but uncertain in
their external application, the weight is medium — significant but not the primary
design challenge.

### `statistical_corrections: M`

When a simulation study compares alternative system configurations or parameter
settings, multiple comparisons arise in the same way as in any designed experiment. Law
and Kelton [L1] §12.3 discuss procedures for comparing means across alternatives,
including Bonferroni corrections and ranking-and-selection methods. The medium weight
reflects that multiplicity is a real concern in comparative simulation studies but is
often managed by the structured experimental design (e.g., paired replications via
common random numbers) rather than post-hoc corrections alone.

---

## 5. `factorial_design`

Factorial experiments manipulate two or more factors simultaneously, allowing estimation
of both main effects and interaction effects. They are the primary vehicle for testing
hypotheses about how factors combine.

### `causal_structure: H`

Full factorial designs with randomisation provide the strongest causal infrastructure
of any standard experimental design. Montgomery [M1] Chapter 5 explains that the
factorial structure allows unconfounded estimation of every main effect and interaction
simultaneously, and that randomisation to factor-level combinations justifies causal
interpretation of all effects. The high weight reflects that causal structure is not
merely an aspiration but a built-in property of the design — the reason factorial
experiments are preferred over one-factor-at-a-time investigations.

### `statistical_corrections: H`

A full two-level factorial design with k factors yields 2^k − 1 effects (k main
effects plus all interactions). Montgomery [M1] §6.4 discusses the multiplicity
problem in detail: with many contrasts, the familywise error rate climbs rapidly. Common
approaches include Lenth's method for unreplicated factorials, Bonferroni correction
for planned comparisons, and graphical half-normal plots to distinguish signal from
noise. The high weight reflects the systematic multiplicity inherent in factorial
designs rather than researcher degrees of freedom.

### `variance_protocol: M`

Blocking and split-plot structures are common in factorial designs when complete
randomisation is impractical. Montgomery [M1] Chapter 7 covers randomised complete
block designs and their factorial extensions, showing that the choice of blocking factor
and the associated error term affect the power of tests for each effect. The medium
weight reflects that variance protocol decisions are important but are resolved at the
design stage rather than constituting an ongoing analytical challenge.

### `ecological_validity: L`

Factorial designs are optimised for internal validity: the careful crossing of factor
levels and randomisation of units to cells gives precise causal estimates within the
experiment. This precision often comes at the cost of external validity — factor levels
may be set at values chosen for orthogonality rather than for representativeness of
real-world conditions. The low weight reflects the design's deliberate prioritisation
of internal over external validity, not a deficiency.

---

## 6. `single_subject`

Single-subject research designs — also called single-case experimental designs —
examine behaviour change within one or a small number of individuals using repeated
measurement and phase alternation (e.g., ABAB reversal, multiple baseline).

### `causal_structure: M`

Single-subject designs can support causal inference through intra-subject replication:
demonstrating that behaviour changes when and only when the intervention is introduced
[Ka1]. Kazdin [Ka1] Chapter 4 explains that the logic of replication within a single
participant — across phases, across baselines, across participants in a multiple-baseline
design — provides the same inferential warrant that between-group replication provides
in group experiments. However, the causal inference is limited to the individual(s)
studied and may not generalise, which prevents elevation to a high weight.

### `ecological_validity: H`

Single-subject research emerged from applied behaviour analysis, where interventions are
almost always implemented in naturalistic settings: schools, clinics, homes, workplaces.
Kazdin [Ka1] Chapter 2 emphasises ecological fidelity as a design value: the
intervention must work in the environment where behaviour naturally occurs, not in a
specially constructed laboratory environment. This applied orientation makes ecological
validity a primary design concern warranting a high weight.

### `variance_protocol: L`

The inferential logic of single-subject designs rests on visual analysis of graphed
data — the stability of baseline phases and the magnitude and immediacy of change upon
intervention — rather than on probabilistic sampling theory [Ka1] Chapter 8. Effect
size indices for single-case designs (PND, IRD, Tau-U) exist but have not supplanted
visual analysis as the primary judgement criterion. Because there is no probability
sample from a population to manage, the variance-protocol machinery of randomised group
designs (power analysis, allocation ratios, stratification) is largely inapplicable,
justifying a low weight.

---

## 7. `qualitative_interpretive`

Qualitative and interpretive research traditions — phenomenology, grounded theory,
ethnography, case study — seek to understand meaning, process, and context through
non-numerical data (interviews, observations, documents).

### `ecological_validity: H`

The central commitment of naturalistic inquiry is to study phenomena in their natural
settings rather than in controlled conditions. Lincoln and Guba [LG1] Part II articulate
"prolonged engagement" and "persistent observation" in the field as core credibility
strategies precisely because ecological authenticity is the design's primary source of
validity. An interview conducted in a participant's workplace or home, or an ethnographic
observation of naturally occurring interaction, maximises ecological validity by
definition. The high weight reflects this foundational commitment.

### `causal_structure: L`

Lincoln and Guba [LG1] Chapter 1 explicitly reject linear causality as a goal of
naturalistic inquiry, arguing instead for mutual, simultaneous shaping — the idea that
causes and effects are so intertwined in human social systems that isolating a
unidirectional causal chain is neither feasible nor theoretically appropriate. This is
not a weakness but a deliberate epistemological stance: the goal is to understand how
things are related and what they mean, not to establish that X produces Y. The low
weight follows directly from this stance.

### `statistical_corrections: L`

Null hypothesis significance testing does not apply in qualitative research. There are
no sampling distributions, no alpha levels, and no multiple-comparison problems in the
classical statistical sense. Credibility and trustworthiness are established through
other means — member checking, triangulation, negative case analysis, audit trails
[LG1] Chapter 11 — none of which involve statistical correction. A low weight reflects
the structural inapplicability of this dimension, not a quality deficit.

### `measurement_alignment: M`

Qualitative research employs a trustworthiness framework in place of the
reliability/validity framework used in quantitative research [LG1]. Transferability
(the qualitative analogue of external validity) requires thick description so that
readers can judge applicability to their own contexts. Dependability (the analogue of
reliability) is established through an inquiry audit. These mechanisms partially address
the concerns captured by measurement alignment, making this dimension relevant at a
medium level — present but not the primary analytical challenge.

### `variance_protocol: L`

Qualitative research uses purposive or theoretical sampling rather than probability
sampling. The goal is to select cases that are information-rich for the phenomenon under
study [LG1] Chapter 9, not to obtain a representative sample from a defined population.
Because there is no sampling distribution to manage, the variance-protocol machinery
(sample-size formulae, power analysis, stratification) is structurally inapplicable.
The low weight reflects this difference in sampling logic.

---

## Reference List

[B1] Borenstein, M., Hedges, L. V., Higgins, J. P. T., & Rothstein, H. R.
*Introduction to Meta-Analysis*. Wiley, 2009. ISBN 978-0-470-05724-7.

[C1] Higgins, J. P. T., Thomas, J., Chandler, J., Cumpston, M., Li, T., Page, M. J.,
& Welch, V. A. (Eds.). *Cochrane Handbook for Systematic Reviews of Interventions*
(2nd ed.). Wiley, 2019. ISBN 978-1-119-53660-4.

[K1] Kline, P. *Handbook of Psychological Testing* (2nd ed.). Routledge, 1999.
ISBN 978-0-415-21158-1.

[Ka1] Kazdin, A. E. *Single-Case Research Designs: Methods for Clinical and Applied
Settings* (3rd ed.). Oxford University Press, 2020. ISBN 978-0-190-07997-0.

[L1] Law, A. M., & Kelton, W. D. *Simulation Modeling and Analysis* (3rd ed.).
McGraw-Hill, 2000. ISBN 978-0-070-59266-7.

[LG1] Lincoln, Y. S., & Guba, E. G. *Naturalistic Inquiry*. Sage Publications, 1985.
ISBN 978-0-803-92431-4.

[M1] Montgomery, D. C. *Design and Analysis of Experiments* (9th ed.). Wiley, 2017.
ISBN 978-1-119-58906-8.

[R1] Rosenbaum, P. R. *Design of Observational Studies*. Springer, 2010.
ISBN 978-1-441-91212-1.

[R2] Rosenbaum, P. R. *Observational Studies* (2nd ed.). Springer, 2002.
ISBN 978-0-387-98967-9.

[S1] Shadish, W. R., Cook, T. D., & Campbell, D. T. *Experimental and
Quasi-Experimental Designs for Generalized Causal Inference*. Houghton Mifflin, 2002.
ISBN 978-0-395-61556-0.
