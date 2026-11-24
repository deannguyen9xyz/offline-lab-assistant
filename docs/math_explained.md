# The maths in plain words

This page explains the few calculations the tool does, using one small example. The example data are illustrative values from a sensor measured in pM:

| X (pM) | 0 | 1 | 2 | 5 | 10 | 20 |
|---|---|---|---|---|---|---|
| Y (mean response) | 0.021 | 0.034 | 0.046 | 0.088 | 0.152 | 0.271 |
| SD of Y | 0.0011 | 0.0013 | 0.0012 | 0.0019 | 0.0024 | 0.0035 |

## 1. The calibration line

The tool fits the straight line **y = a + b·x** by least squares, which means it chooses the line that is closest to all points at once.

- **b, the slope:** the sensitivity, meaning how much the signal rises per unit of concentration. Example: b = 0.01255 per pM.
- **a, the intercept:** the signal at zero concentration. Example: a = 0.0225, close to the measured blank of 0.021.
- **R²:** how close the points lie to the line (1 = perfect). Example: R² = 0.9992.

## 2. Why R² is not enough

A curve can have a high R². In the synthetic example (`data/example_points.csv`), the signal flattens above 100 nM, yet one straight line through all points still gives R² = 0.991.

**Mandel's test** fits both a straight line and a gently curved line (a parabola). If the curve fits *clearly* better, the response is not linear over that range. "Clearly" means significant at the 99 % level. The tool then removes the highest points one by one until the line passes.

**The lack-of-fit test** asks whether the points scatter around the line more than their own SDs would explain. For this it needs to know how many readings (n) are behind each SD.

## 3. LOD and LOQ

The idea: a signal is "detected" when it stands clearly above the noise of the blank. Converting that noise into concentration gives:

> **LOD = k · σ / b**  **LOQ = 10 · σ / b**

- **σ:** the noise (a standard deviation).
- **b:** the slope, which converts signal into concentration.
- **k:** a safety factor, 3.3 (ICH Q2(R2)) or 3 (the IUPAC "3σ" convention common in sensor papers). With k = 3, every LOD is 9 % lower.

The tool gives two ways to estimate σ, and this is where the numbers differ.

| | Method A: SD of the blank point | Method B: SD around the line (s_y/x) |
|---|---|---|
| σ is… | the SD of the repeated readings at X = 0 | how far the points scatter around the fitted line |
| Describes | noise at zero concentration only | scatter over the whole range, including small deviations from linearity |
| Example σ | 0.0011 | 0.00299 |
| Example LOD | 3.3 × 0.0011 / 0.01255 = **0.29 pM** | 3.3 × 0.00299 / 0.01255 = **0.79 pM** |

**Why they differ:** method A only "sees" the blank. Method B also includes any error of the line, such as slight curvature or a standard that was diluted a little off. In this example B is 2.7 times larger, because the points scatter around the line more than the blank readings scatter.

**What to report:** both are accepted ways of doing the calculation. Method B is usually the more cautious one. Whichever you use, state which σ you used and what k is. Both LODs here lie below the lowest standard (1 pM), so they are estimates; a standard near the LOQ confirms them.

The tool also gives a third version, B2, which uses the standard error of the intercept as σ (an alternative named in ICH Q2).

## 4. One more check: is the noise the same everywhere?

In the example, the SD grows from 0.0011 at 0 pM to 0.0035 at 20 pM. Ordinary least squares treats all points as equally precise. A **weighted fit** (weights 1/SD²) trusts the precise low points more. If the two slopes differ by more than the slope's own uncertainty, weighting matters, and the tool flags it.

## References

- L. A. Currie, Nomenclature in evaluation of analytical methods including detection and quantification capabilities (IUPAC Recommendations 1995), *Pure Appl. Chem.* 67 (1995) 1699–1723. DOI: [10.1351/pac199567101699](https://doi.org/10.1351/pac199567101699)
- A. Shrivastava, V. B. Gupta, Methods for the determination of limit of detection and limit of quantitation of the analytical methods, *Chron. Young Sci.* 2 (2011) 21. DOI: [10.4103/2229-5186.79345](https://doi.org/10.4103/2229-5186.79345)
- J. M. Andrade, M. P. Gómez-Carracedo, Notes on the use of Mandel's test to check for nonlinearity in laboratory calibrations, *Anal. Methods* (2013). DOI: [10.1039/c2ay26400e](https://doi.org/10.1039/c2ay26400e)
- ICH Q2(R2), *Validation of Analytical Procedures* (2023): the source of k = 3.3 and of the two σ options. It is a guideline with no DOI; it is available on ich.org.
