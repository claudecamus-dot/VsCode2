<#
.SYNOPSIS
  Crop a rectangle out of a PNG for close inspection.
.DESCRIPTION
  Copies the (X,Y,W,H) pixel region of -In into -Out at 1:1. Use after rendering a
  slide at high resolution (e.g. 2560x1440) to zoom on a suspect area — number-beside-
  a-bar alignment, the bottom-right page-number badge, a card edge — then Read the crop.
.EXAMPLE
  pwsh crop-png.ps1 -In slide-04.png -Out corner.png -X 1980 -Y 1180 -W 620 -H 300
#>
param(
  [Parameter(Mandatory = $true)][string]$In,
  [Parameter(Mandatory = $true)][string]$Out,
  [Parameter(Mandatory = $true)][int]$X,
  [Parameter(Mandatory = $true)][int]$Y,
  [Parameter(Mandatory = $true)][int]$W,
  [Parameter(Mandatory = $true)][int]$H
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$In = (Resolve-Path $In).Path
$img = [System.Drawing.Image]::FromFile($In)
try {
  # Clamp the source rectangle to the image bounds (avoid an out-of-range crop).
  $x = [Math]::Max(0, [Math]::Min($X, $img.Width - 1))
  $y = [Math]::Max(0, [Math]::Min($Y, $img.Height - 1))
  $w = [Math]::Min($W, $img.Width - $x)
  $h = [Math]::Min($H, $img.Height - $y)
  $b = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($b)
  $src = New-Object System.Drawing.Rectangle $x, $y, $w, $h
  $dst = New-Object System.Drawing.Rectangle 0, 0, $w, $h
  $g.DrawImage($img, $dst, $src, [System.Drawing.GraphicsUnit]::Pixel)
  $b.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $b.Dispose()
} finally {
  $img.Dispose()
}
Write-Output (Resolve-Path $Out).Path
