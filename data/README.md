# Data folder

Put your calibration files here. The assistant can open any CSV or TXT file in this folder by name.

**Format:** one row per concentration level, with three columns:

| Column | Meaning |
|---|---|
| `concentration` | concentration of the prepared standard (any unit: nM, pM, µg/L …) |
| `response` | mean sensor response at that concentration |
| `sd` | standard deviation of the response (from the replicate readings) |

```
concentration,response,sd
0,0.013,0.0008
20,0.050,0.0017
40,0.106,0.0021
```

- A row at concentration 0 is the blank; its SD is used for the blank-based LOD.
- Any number of rows from 3 upwards; at least 5 are needed for the curvature test.
- Excel files saved as CSV with semicolons and decimal commas also work.

**Privacy:** files in this folder are ignored by git (see `.gitignore`), so your measurements are not uploaded if you publish the repository. Only `example_points.csv`, a synthetic data set, is shared.
