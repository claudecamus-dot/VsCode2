<#
.SYNOPSIS
  Render a .pptx to per-slide PNGs via PowerPoint COM (Windows).
.DESCRIPTION
  Opens the presentation read-only with no window, exports each slide (or a chosen
  subset) to slide-NN.png in -OutDir, and prints the written paths. Read the PNGs to
  eyeball the design (see the pptx-verify skill checklist).
.EXAMPLE
  pwsh render-pptx.ps1 -Pptx deck.pptx -OutDir ./out
  pwsh render-pptx.ps1 -Pptx deck.pptx -OutDir ./out -Width 2560 -Height 1440 -Slides 3,6
#>
param(
  [Parameter(Mandatory = $true)][string]$Pptx,
  [string]$OutDir = ".",
  [int]$Width = 1600,
  [int]$Height = 900,
  [int[]]$Slides = @()        # e.g. -Slides 1,3,6 ; omit = all slides
)
$ErrorActionPreference = "Stop"
$Pptx = (Resolve-Path $Pptx).Path
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
$OutDir = (Resolve-Path $OutDir).Path

$p = New-Object -ComObject PowerPoint.Application
try {
  # Open(path, ReadOnly, Untitled, WithWindow)
  $pres = $p.Presentations.Open($Pptx, $true, $false, $false)
  try {
    $i = 0
    foreach ($s in $pres.Slides) {
      $i++
      if ($Slides.Count -gt 0 -and ($Slides -notcontains $i)) { continue }
      $png = Join-Path $OutDir ("slide-{0:D2}.png" -f $i)
      $s.Export($png, "PNG", $Width, $Height)
      Write-Output $png
    }
  } finally {
    $pres.Close()
  }
} finally {
  $p.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($p) | Out-Null
}
