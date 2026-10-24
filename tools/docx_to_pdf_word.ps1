# Проверка вида docx в установленном Microsoft Word (только для разработки на Windows).
# Файл сохранён в UTF-8 с BOM: без него Windows PowerShell 5.1 читает кириллицу как ANSI.
# Открывает документ только для чтения, печатает число страниц и сохраняет PDF.
# В продукте PDF делает LibreOffice (documentation/ARCHITECTURE.md, раздел 6.3); Word нужен, чтобы видеть
# документ так же, как его откроет менеджер.
#
#   powershell -File tools\docx_to_pdf_word.ps1 -Docx C:\путь\protocol.docx -Pdf C:\путь\protocol.pdf
param(
    [Parameter(Mandatory = $true)][string]$Docx,
    [Parameter(Mandatory = $true)][string]$Pdf
)
$word = New-Object -ComObject Word.Application
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open((Resolve-Path $Docx).Path, $false, $true)
    try {
        Write-Output ("Страниц: " + $document.ComputeStatistics(2))
        # Относительный путь считается от текущей папки PowerShell, а не процесса.
        $target = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Pdf)
        $document.ExportAsFixedFormat($target, 17)
        Write-Output ("PDF: " + $target)
    } finally {
        $document.Close(0)
    }
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
