# Run experiment suite and generate summary
param(
    [string]$Mode = "fake",
    [string]$Name = "context_ablation",
    [string]$Report = "reports/exp_context_ablation.json",
    [string]$Summary = "docs/evidence/experiment_summary.md"
)

Write-Host "=== Running experiment: $Name ($Mode mode) ==="
python -m minibot experiments run --name $Name --mode $Mode --report $Report

if ($LASTEXITCODE -ne 0) {
    Write-Host "Experiment failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "=== Generating summary ==="
python -m minibot experiments summarize --reports $Report --output $Summary

Write-Host "=== Done ==="
Write-Host "Report: $Report"
Write-Host "Summary: $Summary"
