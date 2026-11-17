#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Executable, [Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') { throw 'Run this isolated WPF renderer with Windows PowerShell -STA.' }
Add-Type -AssemblyName PresentationFramework
$output = [IO.Path]::GetFullPath($OutputDirectory)
[void][IO.Directory]::CreateDirectory($output)
$fixture = Join-Path $output 'offline-fixture'
$config = Join-Path $fixture 'config'
[void][IO.Directory]::CreateDirectory($config)
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $config 'frpc.toml'),"serverAddr = '198.51.100.20'`r`nserverPort = 7000`r`n",$utf8)
[IO.File]::WriteAllText((Join-Path $config 'model-settings.json'),'{"model":"qwen3-8b-8k:latest"}',$utf8)
$routeData = @{
    SchemaVersion=1
    Routes=@(
        @{Name='示例国内线路'; Address='198.51.100.20'; Region='离线示例'; CountryCode='CN'; Location='手动填写位置'},
        @{Name='示例备用线路'; Address='192.0.2.10'; Region='离线示例'; CountryCode='JP'; Location='手动填写位置'},
        @{Name='示例第三线路'; Address='203.0.113.30'; Region='离线示例'; CountryCode='US'; Location='手动填写位置'}
    )
}
[IO.File]::WriteAllText((Join-Path $config 'panel-routes.json'),($routeData|ConvertTo-Json -Depth 6),$utf8)

$assembly = [Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($Executable))
$type = $assembly.GetType('LocalAIControl.Panel',$true)
$constructor = $type.GetConstructor([type[]]@([string]))
$panel = $constructor.Invoke([object[]]@([string]$fixture))
$window = $panel.Window
try {
    # Do not Show() the window: ContentRendered starts the production status
    # query and probe timer. Render its real WPF content while it remains hidden.
    $tunnel = [Collections.Generic.Dictionary[string,object]]::new()
    $tunnel['State']='stopped'; $tunnel['Text']='未启动'; $tunnel['Detail']='CI 离线夹具 · 无真实隧道'
    $state = [Collections.Generic.Dictionary[string,object]]::new()
    $state['OllamaOnline']=$false; $state['ModelInstalled']=$false; $state['TailscaleConnected']=$false
    $state['Tunnel']=$tunnel; $state['FrpcCount']=0; $state['Server']='198.51.100.20'; $state['Model']='qwen3-8b-8k:latest'
    $flags = [Reflection.BindingFlags]'NonPublic,Instance'
    [void]$type.GetMethod('Apply',$flags).Invoke($panel,[object[]]@($state))
    $window.FindName('CheckedAt').Text='CI 离线布局预览 · 不执行真实状态查询'
    $window.FindName('BannerText').Text='这是 Windows CI 上由原生 WPF 源码渲染的离线界面。示例线路不可连接；未运行模型或隧道。'
    $window.FindName('HeaderState').Text='CI 离线预览'

    $content = $window.Content
    $window.Content = $null
    $root = [Windows.Controls.Grid]::new()
    $root.Resources = $window.Resources
    $root.Width=980; $root.Height=900; $root.Background=[Windows.Media.Brushes]::White
    $top = [Windows.Controls.RowDefinition]::new(); $top.Height=[Windows.GridLength]::new(40)
    $body = [Windows.Controls.RowDefinition]::new(); $body.Height=[Windows.GridLength]::new(860)
    $root.RowDefinitions.Add($top); $root.RowDefinitions.Add($body)
    $label = [Windows.Controls.TextBlock]::new()
    $label.Text='Windows CI · 原生 WPF 离线布局预览（非用户电脑实拍 / 非连通性验收）'
    $label.FontFamily=[Windows.Media.FontFamily]::new('Microsoft YaHei UI')
    $label.FontSize=15; $label.FontWeight=[Windows.FontWeights]::SemiBold
    $label.Foreground=[Windows.Media.Brushes]::DarkSlateBlue
    $label.VerticalAlignment='Center'; $label.Margin=[Windows.Thickness]::new(18,0,0,0)
    [void]$root.Children.Add($label)
    [Windows.Controls.Grid]::SetRow($content,1); [void]$root.Children.Add($content)
    $root.Measure([Windows.Size]::new(980,900))
    $root.Arrange([Windows.Rect]::new(0,0,980,900)); $root.UpdateLayout()
    $bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new(980,900,96,96,[Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($root)
    $encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new()
    $encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $path=Join-Path $output 'windows-ci-offline-preview.png'
    $stream=[IO.File]::Create($path)
    try {$encoder.Save($stream)} finally {$stream.Dispose()}
    if ((Get-Item -LiteralPath $path).Length -lt 10000) { throw 'WPF preview is unexpectedly empty.' }
    $metadata=[pscustomobject]@{
        Schema=1; Commit=$env:GITHUB_SHA; Image='windows-ci-offline-preview.png'; Width=980; Height=900
        Source='Original compiled LocalAIControl.Panel and embedded XAML, rendered by Windows WPF.'
        Scope='Hidden offline fixture. No Window.Show, production backend queries, probe timer, service action or network call.'
        UserDesktopScreenshot=$false; ModelConnectivityTest=$false
    }
    [IO.File]::WriteAllText((Join-Path $output 'offline-preview.json'),($metadata|ConvertTo-Json -Depth 4),$utf8)
    Write-Output 'Native Windows WPF offline preview rendered; no live services were accessed.'
} finally {
    $window.Close()
}
