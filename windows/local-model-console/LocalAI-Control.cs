using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

[assembly: AssemblyTitle("Local AI Control")]
[assembly: AssemblyProduct("远程模型控制台")]
[assembly: AssemblyVersion("1.1.2.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

namespace LocalAIControl {
 public sealed class Route : INotifyPropertyChanged {
  public string Name {get;set;} public string Address {get;set;} public string Region {get;set;}
  public string CountryCode {get;set;} public string GeoCode {get;set;} public string GeoName {get;set;} public string GeoCheckedAt {get;set;}
  public string GeoRegion {get;set;} public string GeoCity {get;set;} public int GeoVersion {get;set;} public string Location {get;set;}
  string latency="待检测",ping="Ping 待检测"; Brush latencyColor=Brushes.SlateGray; ImageSource flag;
  public event PropertyChangedEventHandler PropertyChanged;
  [ScriptIgnore] public string EffectiveCountry {get{return String.IsNullOrEmpty(CountryCode)?GeoCode:CountryCode;}}
  [ScriptIgnore] public string CountryDisplay {get{return CountryNames.Name(EffectiveCountry,GeoName);}}
  [ScriptIgnore] public string LocationDisplay {get{if(!String.IsNullOrEmpty(Location))return Location;string value=String.Join(" · ",new[]{GeoRegion,GeoCity}.Where(x=>!String.IsNullOrEmpty(x)).Distinct());return String.IsNullOrEmpty(value)?"省 / 州、城市待识别":value.Replace("科羅拉多","科罗拉多");}}
  [ScriptIgnore] public string LocationTip {get{return (String.IsNullOrEmpty(Location)?"IP 地理库位置，可编辑修正：":"手动标注位置：")+CountryDisplay+" · "+LocationDisplay;}}
  [ScriptIgnore] public string LatencyText {get{return latency;}}
  [ScriptIgnore] public string PingText {get{return ping;}}
  [ScriptIgnore] public Brush PingColor {get{return ping.EndsWith(" ms",StringComparison.Ordinal)?Brushes.SeaGreen:Brushes.SlateGray;}}
  [ScriptIgnore] public Brush LatencyColor {get{return latencyColor;}}
  [ScriptIgnore] public ImageSource Flag {get{return flag;}}
  public void UpdateProbe(string value,string pingValue,bool reachable,ImageSource image) {
   latency=value;ping=pingValue;latencyColor=reachable?new SolidColorBrush(Color.FromRgb(22,133,101)):Brushes.DarkGoldenrod;flag=image;
   foreach(string key in new[]{"LatencyText","PingText","PingColor","LatencyColor","CountryDisplay","LocationDisplay","LocationTip","Flag"})if(PropertyChanged!=null)PropertyChanged(this,new PropertyChangedEventArgs(key));
  }
 }
 public static class CountryNames {
  static readonly Dictionary<string,string> known=new Dictionary<string,string>{{"US","美国"},{"CN","中国"},{"HK","中国香港"},{"TW","中国台湾"},{"MO","中国澳门"},{"SG","新加坡"},{"JP","日本"},{"KR","韩国"},{"GB","英国"},{"DE","德国"},{"FR","法国"},{"CA","加拿大"},{"AU","澳大利亚"},{"NL","荷兰"},{"RU","俄罗斯"},{"IN","印度"}};
  public static string Name(string code,string fallback) {
   if(String.IsNullOrEmpty(code))return "未识别";
   string name;if(known.TryGetValue(code.ToUpperInvariant(),out name))return name;
   try{return new RegionInfo(code).DisplayName;}catch{return String.IsNullOrEmpty(fallback)?code:fallback;}
  }
 }
 public sealed class RouteFile { public int SchemaVersion {get;set;} public List<Route> Routes {get;set;} }
 public static class Program {
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle,int command);
  public static string ResourceText(string name) {
   using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
   using(var reader=new StreamReader(stream,Encoding.UTF8,true)) return reader.ReadToEnd();
  }
  public static string ResolveRoot(string preferred) {
   string exe=AppDomain.CurrentDomain.BaseDirectory;
   var paths=new List<string>();
   if(!String.IsNullOrEmpty(preferred)) paths.Add(preferred);
   else {
    paths.Add(exe); paths.Add(Path.GetFullPath(Path.Combine(exe,".."))); paths.Add(Path.GetFullPath(Path.Combine(exe,"..","..")));
    string doc=Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
    if(!String.IsNullOrEmpty(doc)) paths.Add(Path.Combine(doc,"LocalProjects","projects","qwen3-remote"));
    paths.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"Documents","LocalProjects","projects","qwen3-remote"));
   }
   foreach(string path in paths) if(File.Exists(Path.Combine(path,"config","frpc.toml"))&&File.Exists(Path.Combine(path,"scripts","start-services.ps1"))) return Path.GetFullPath(path).TrimEnd('\\');
   throw new InvalidOperationException("找不到 qwen3-remote 项目。请把 EXE 放入项目 tools\\control-panel，或用 --project-root 指定已有项目。");
  }
  [STAThread] public static void Main(string[] args) {
   try {
    // Let Windows select modern TLS; do not inherit the legacy compiler's TLS 1.0 default.
    ServicePointManager.SecurityProtocol=SecurityProtocolType.SystemDefault;
    string preferred=null; for(int i=0;i<args.Length;i++) if(args[i]=="--project-root"&&i+1<args.Length) preferred=args[++i];
    string root=ResolveRoot(preferred);
    bool created; using(var mutex=new Mutex(true,"Local\\LocalAIControlNative-"+Environment.UserName,out created)) {
     if(!created) {
      foreach(var p in Process.GetProcessesByName(Process.GetCurrentProcess().ProcessName)) if(p.Id!=Process.GetCurrentProcess().Id&&p.MainWindowHandle!=IntPtr.Zero) { ShowWindow(p.MainWindowHandle,9); SetForegroundWindow(p.MainWindowHandle); break; }
      return;
     }
     try { var app=new Application(); var panel=new Panel(root); app.Run(panel.Window); }
     finally { mutex.ReleaseMutex(); }
    }
   } catch(Exception ex) { MessageBox.Show(ex.Message,"远程模型控制台",MessageBoxButton.OK,MessageBoxImage.Error); }
  }
 }
 public sealed partial class Panel {
  public Window Window {get;private set;}
  readonly string root,backend,startScript,routesPath;
  readonly bool externalMetadataEnabled;
  readonly JavaScriptSerializer json=new JavaScriptSerializer {MaxJsonLength=2097152};
  readonly ObservableCollection<Route> routes=new ObservableCollection<Route>();
  readonly SemaphoreSlim gate=new SemaphoreSlim(1,1);
  readonly DispatcherTimer timer=new DispatcherTimer();
  readonly SemaphoreSlim probeLimit=new SemaphoreSlim(4,4);
  Dictionary<string,object> state;
  bool busy,checking,closed,routeInitialized,probing;
  DateTime lastProbe=DateTime.MinValue;
  string actionError="";
  T Control<T>(string name) where T:class { return Window.FindName(name) as T; }
  Button Button(string name) {return Control<Button>(name);}
  TextBlock Text(string name) {return Control<TextBlock>(name);}
  ListBox List {get {return Control<ListBox>("RouteList");}}
  Route Selected {get {return List.SelectedItem as Route;}}
  static Brush Brush(string value) { return (Brush)new BrushConverter().ConvertFromString(value); }
  static string S(Dictionary<string,object> item,string key) {object value; return item!=null&&item.TryGetValue(key,out value)&&value!=null?Convert.ToString(value):"";}
  static bool B(Dictionary<string,object> item,string key) {object value; return item!=null&&item.TryGetValue(key,out value)&&value is bool&&(bool)value;}
  static Dictionary<string,object> D(Dictionary<string,object> item,string key) {object value; return item!=null&&item.TryGetValue(key,out value)?value as Dictionary<string,object>:null;}
  static bool SameAddress(string left,string right) {return String.Equals(left,right,StringComparison.OrdinalIgnoreCase);}
  static string Q(string text) {return "\""+text.Replace("\"","\\\"")+"\"";}
  public Panel(string projectRoot) {
   root=projectRoot; externalMetadataEnabled=ReadMetadataPreference(root); routesPath=Path.Combine(root,"config","panel-routes.json");
   string folder=Path.Combine(root,"state","panel-runtime"); Directory.CreateDirectory(folder);
   backend=Path.Combine(folder,"LocalAI-Backend.ps1");
   string source=Program.ResourceText("LocalAI.Backend.ps1");
   if(!File.Exists(backend)||File.ReadAllText(backend,Encoding.UTF8)!=source) AtomicText(backend,source,new UTF8Encoding(true));
   startScript=Path.Combine(folder,"Start-Services-STCP.ps1");
   string startSource=Program.ResourceText("LocalAI.StartServices.ps1");
   if(!File.Exists(startScript)||File.ReadAllText(startScript,Encoding.UTF8)!=startSource)AtomicText(startScript,startSource,new UTF8Encoding(true));
   Window=(Window)XamlReader.Parse(Program.ResourceText("LocalAI.UI.xaml"));
   LoadRoutes(); List.ItemsSource=routes;
   List.SelectionChanged+=(s,e)=> {if(!busy)UpdateStatusBanner();UpdateButtons();};
   Button("StartButton").Click+=async(s,e)=>{string action=PrimaryActionName;if(action!="StopAll"||ConfirmStop(action))await Action(action);};
   Button("ResumeButton").Click+=async(s,e)=>await Action("Start");
   Button("GlobalStopButton").Click+=async(s,e)=>{if(ConfirmStop("StopAll"))await Action("StopAll");};
   Button("StopButton").Click+=async(s,e)=>await Action("Stop");
   Button("StopOllamaButton").Click+=async(s,e)=>{if(ConfirmStop("StopOllama"))await Action("StopOllama");};
   Button("ConnectionButton").Click+=(s,e)=>{try{ConnectionWindow();}catch{Banner("连接偏好无法读取，请检查面板设置文件。","#FFF0D8");}};
   Button("HistoryButton").Click+=(s,e)=>HistoryWindow();
   Button("SettingsButton").Click+=(s,e)=>SettingsWindow();
   Button("ModelButton").Click+=(s,e)=>ModelWindow();
   Button("OllamaSettingsButton").Click+=(s,e)=>SettingsWindow("", "model");
   Button("FrpSettingsButton").Click+=(s,e)=>SettingsWindow("", "frps");
   Button("TailscaleInfoButton").Click+=(s,e)=>TailscaleWindow();
   Button("WebButton").Click+=async(s,e)=>await OpenWeb();
   List.AddHandler(System.Windows.Controls.Button.ClickEvent,new RoutedEventHandler((s,e)=>{var button=e.OriginalSource as Button;if(button!=null&&button.Tag is Route){e.Handled=true;ExportRoute((Route)button.Tag);}}));
   Button("CherryButton").Click+=async(s,e)=>await Action("Cherry");
   Button("RefreshButton").Click+=async(s,e)=>{actionError="";await Refresh();};
   Button("AddRouteButton").Click+=(s,e)=>EditRoute(false);
   Button("EditRouteButton").Click+=(s,e)=>EditRoute(true);
   Button("DeleteRouteButton").Click+=(s,e)=>DeleteRoute();
   Button("ProbeButton").Click+=async(s,e)=>await ProbeRoutes(true);
   Button("UpdateButton").Click+=(s,e)=>{try{Process.Start(new ProcessStartInfo("https://github.com/dneo9527-ctrl/remote-local-model-frpc/releases"){UseShellExecute=true});History("已打开同一项目的版本下载页。下载与校验完成后再退出面板更新；本次未替换程序。");}catch{History("无法打开版本页，请手动访问本项目 GitHub Releases。");}};
   timer.Interval=TimeSpan.FromSeconds(15); timer.Tick+=async(s,e)=>{if(!Window.IsVisible||Window.WindowState==WindowState.Minimized)return;await Refresh();if((DateTime.UtcNow-lastProbe).TotalSeconds>=30)await ProbeRoutes();};
   Window.ContentRendered+=async(s,e)=>{await Refresh();timer.Start();await ProbeRoutes();};
   Window.Closing+=(s,e)=>{if(busy){e.Cancel=true;Banner("操作仍在进行，请完成后再关闭窗口。","#FFF0D8");}};
   Window.Closed+=(s,e)=>{closed=true;timer.Stop();};
   UpdateButtons();
  }
  static void AtomicText(string path,string text,Encoding encoding,bool keepPrevious=false) {
   if(File.Exists(path)&&File.ReadAllText(path,encoding)==text)return;
   string temp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
   try {File.WriteAllText(temp,text,encoding);if(File.Exists(path)) File.Replace(temp,path,keepPrevious?path+".previous":null);else File.Move(temp,path);}
   finally {if(File.Exists(temp))File.Delete(temp);}
  }
  public static void ValidateRoute(Route route) {
   if(route==null||String.IsNullOrWhiteSpace(route.Name)||route.Name.Length>80) throw new ArgumentException("请填写线路名称（最多 80 字）。");
   string host=route.Address??"";
   if(host.Length==0||host.Length>253||Uri.CheckHostName(host)==UriHostNameType.Unknown||host.Any(c=>!Char.IsLetterOrDigit(c)&&c!='.'&&c!='-'&&c!=':')) throw new ArgumentException("服务器需为 IP 或域名，不含 http://、端口、路径或空格。");
   if(host.Any(c=>c>127)) throw new ArgumentException("域名请使用 ASCII / Punycode 格式。");
   if((route.Region??"").Length>40) throw new ArgumentException("备注最多 40 字。");
   if((route.Location??"").Length>120)throw new ArgumentException("位置标注最多 120 字。");
  }
  void LoadRoutes() {
   if(File.Exists(routesPath)) {
    try {
     var saved=json.Deserialize<RouteFile>(File.ReadAllText(routesPath,Encoding.UTF8));
     if(saved==null||saved.SchemaVersion!=1||saved.Routes==null)throw new InvalidDataException("不支持的线路列表格式。");
     foreach(var route in saved.Routes){ValidateRoute(route);routes.Add(route);}
    }catch(Exception ex){throw new InvalidDataException("panel-routes.json 无法读取或格式不正确，已保留原文件。"+(File.Exists(routesPath+".previous")?"可在备份当前文件后，从 panel-routes.json.previous 恢复上次手动编辑前的列表。":"请从项目备份恢复线路列表。"),ex);}
   } else {
    routes.Add(new Route{Name="HHost",Region="海外",Address="192.0.2.10"});
    routes.Add(new Route{Name="三丰云",Region="国内",Address="198.51.100.20"});
    routes.Add(new Route{Name="阿里云",Region="frps 待安装",Address="203.0.113.30"});SaveRoutes();
   }
  }
  // Manual edits alone rotate the recovery copy. Probe results share the UI thread and
  // save the current collection after awaiting, so an in-flight probe cannot restore
  // an already deleted/replaced Route or rotate a manual recovery point.
  void SaveRoutes(bool keepPrevious=false) {AtomicText(routesPath,json.Serialize(new RouteFile{SchemaVersion=1,Routes=routes.ToList()}),new UTF8Encoding(false),keepPrevious);}
  void EditRoute(bool edit) {
   if(busy||(edit&&Selected==null)) return;
   Route old=edit?Selected:null;
   var dialog=new RouteDialog(old);dialog.Owner=Window;dialog.Resources=Window.Resources;
   while(dialog.ShowDialog()==true) {
    Route route=dialog.Result;
    try {
     ValidateRoute(route);
     if(routes.Any(r=>r!=old&&SameAddress(r.Address,route.Address))) throw new ArgumentException("同一服务器地址已在列表中，请编辑已有线路。");
     int index=old==null?routes.Count:routes.IndexOf(old);
     var previous=routes.ToList();
     if(old!=null)routes.Remove(old);routes.Insert(index,route);
     try {SaveRoutes(true);}catch {routes.Clear();foreach(var r in previous)routes.Add(r);throw;}
     List.SelectedItem=route;List.ScrollIntoView(route);lastProbe=DateTime.MinValue;History((edit?"已编辑线路：":"已添加线路：")+route.Name+"。实际配置在点击启动后才修改。");return;
    }catch(Exception ex){MessageBox.Show(Window,ex.Message,"线路未保存",MessageBoxButton.OK,MessageBoxImage.Information);dialog=new RouteDialog(route);dialog.Owner=Window;dialog.Resources=Window.Resources;}
   }
  }
  void DeleteRoute() {
   if(busy||Selected==null)return;
   Route old=Selected;
   if(MessageBox.Show(Window,"从列表删除“"+old.Name+"”？这不会删除 VPS、修改 frpc.toml 或停止当前连接。\n删除前的列表将保存在 panel-routes.json.previous，自动测速不会覆盖它。","删除列表项",MessageBoxButton.OKCancel,MessageBoxImage.Question)!=MessageBoxResult.OK)return;
   int index=routes.IndexOf(old);routes.Remove(old);
   try {SaveRoutes(true);if(routes.Count>0)List.SelectedIndex=Math.Min(index,routes.Count-1);History("已移除列表项："+old.Name+"；当前连接不变。");}
   catch(Exception ex){routes.Insert(index,old);List.SelectedItem=old;Banner(ex.Message,"#FCE8E8");}
   UpdateButtons();
  }
  sealed class ProbeResult {public string Latency,Ping,Code,Country,Checked,Region,City;public int Version;public bool Reachable;public byte[] Flag;}
  static byte[] Fetch(string url) {
   var request=(HttpWebRequest)WebRequest.Create(url);request.Timeout=3500;request.ReadWriteTimeout=3500;request.UserAgent="LocalAI-Control/1.0";
   using(var response=request.GetResponse())using(var input=response.GetResponseStream())using(var output=new MemoryStream()){input.CopyTo(output);return output.ToArray();}
  }
  static bool IsPrivate(IPAddress ip) {
   if(IPAddress.IsLoopback(ip)||ip.IsIPv6LinkLocal||ip.IsIPv6SiteLocal||ip.IsIPv6Multicast)return true;
   byte[] b=ip.GetAddressBytes();if(b.Length==16)return (b[0]&254)==252;
   return b[0]==0||b[0]==10||b[0]==127||(b[0]==172&&b[1]>=16&&b[1]<=31)||(b[0]==192&&b[1]==168)||(b[0]==169&&b[1]==254)||(b[0]==100&&b[1]>=64&&b[1]<=127)||(b[0]==198&&b[1]==51&&b[2]==100)||(b[0]==203&&b[1]==0&&b[2]==113)||(b[0]==192&&b[1]==0&&b[2]==2);
  }
  static bool ReadMetadataPreference(string project) {
   // Opt-in only: a user can explicitly permit VPS IP disclosure to ipwho.is
   // and flag requests to flagcdn.com. The default performs neither request.
   try {
    string path=Path.Combine(project,"config","panel-preferences.json");
    if(!File.Exists(path))return false;
    var values=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));
    return B(values,"allowExternalMetadata");
   }catch{return false;}
  }
  int ConfiguredProbePort() {
   try {
    string text=File.ReadAllText(Path.Combine(root,"config","frpc.toml"),Encoding.UTF8);
    var table=System.Text.RegularExpressions.Regex.Match(text,@"(?m)^[\t ]*\[");
    if(table.Success)text=text.Substring(0,table.Index);
    var matches=System.Text.RegularExpressions.Regex.Matches(text,@"(?m)^(?:\uFEFF)?[\t ]*serverPort[\t ]*=[\t ]*(?<port>[0-9]+)[\t ]*(?:#[^\r\n]*)?\r?$");
    int port;
    if(matches.Count==0)return System.Text.RegularExpressions.Regex.IsMatch(text,@"(?m)^(?:\uFEFF)?[\t ]*serverPort[\t ]*=")?0:7000; // frp's documented default only when omitted.
    if(matches.Count!=1||!Int32.TryParse(matches[0].Groups["port"].Value,out port)||port<1||port>65535)return 0;
    return port;
   }catch{return 0;}
  }
  byte[] FlagData(string code) {
   if(String.IsNullOrEmpty(code)||code.Length!=2||code.Any(c=>c<'A'||c>'Z'))return null;
   using(var embedded=Assembly.GetExecutingAssembly().GetManifestResourceStream("LocalAI.Flag."+code+".png")) {
    if(embedded!=null){using(var stream=new MemoryStream()){embedded.CopyTo(stream);return stream.ToArray();}}
   }
   string dir=Path.Combine(root,"state","panel-runtime","flags"),file=Path.Combine(dir,code+".png");
   if(File.Exists(file))return File.ReadAllBytes(file);
   if(!externalMetadataEnabled)return null;
   try{byte[] data=Fetch("https://flagcdn.com/w40/"+code.ToLowerInvariant()+".png");if(data.Length>100000)return null;Directory.CreateDirectory(dir);File.WriteAllBytes(file,data);return data;}catch{return null;}
  }
  ProbeResult Measure(Route route) {
   var result=new ProbeResult{Latency="端口未通",Ping="Ping 未响应",Code=route.GeoCode,Country=route.GeoName,Checked=route.GeoCheckedAt,Region=route.GeoRegion,City=route.GeoCity,Version=route.GeoVersion};
   IPAddress ip;
   try {
    if(!IPAddress.TryParse(route.Address,out ip)){var dns=Dns.GetHostAddressesAsync(route.Address);if(!dns.Wait(2000))throw new TimeoutException();ip=dns.Result.OrderBy(x=>x.AddressFamily==AddressFamily.InterNetwork?0:1).First();}
   }catch{result.Latency="地址未解析";return result;}
   int port=ConfiguredProbePort();
   if(port==0){result.Latency="frps 端口配置无效";return result;}
   using(var tcp=new TcpClient(ip.AddressFamily)) {
    var clock=Stopwatch.StartNew();
    try {var connection=tcp.BeginConnect(ip,port,null,null);using(connection.AsyncWaitHandle){if(!connection.AsyncWaitHandle.WaitOne(1800))throw new TimeoutException();tcp.EndConnect(connection);}result.Reachable=true;result.Latency="TCP "+Math.Max(1,clock.ElapsedMilliseconds)+" ms";}catch {result.Latency=port+" 端口未通";}
   }
   using(var ping=new Ping()){try{var reply=ping.Send(ip,1200);if(reply.Status==IPStatus.Success)result.Ping="Ping "+reply.RoundtripTime+" ms";}catch{}}
   DateTime checkedAt;bool cached=route.GeoVersion>=2&&DateTime.TryParse(route.GeoCheckedAt,CultureInfo.InvariantCulture,DateTimeStyles.RoundtripKind,out checkedAt)&&(DateTime.UtcNow-checkedAt.ToUniversalTime()).TotalDays<(String.IsNullOrEmpty(route.GeoCode)?1:7);
   if(externalMetadataEnabled&&String.IsNullOrEmpty(route.CountryCode)&&!cached&&!IsPrivate(ip)) {
    result.Checked=DateTime.UtcNow.ToString("o");result.Version=2;
    try {
     var geo=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(Fetch("https://ipwho.is/"+Uri.EscapeDataString(ip.ToString())+"?lang=zh-CN&fields=success,country_code,country,region,city")));
     string code=S(geo,"country_code").ToUpperInvariant();
     if(B(geo,"success")&&code.Length==2&&code.All(c=>c>='A'&&c<='Z')){result.Code=code;result.Country=S(geo,"country");result.Region=S(geo,"region");result.City=S(geo,"city");}
    }catch{}
   }
   result.Flag=FlagData(String.IsNullOrEmpty(route.CountryCode)?result.Code:route.CountryCode);return result;
  }
  async Task ProbeOne(Route route) {
   await probeLimit.WaitAsync();
   try {
    var result=await Task.Run(()=>Measure(route));
    if(closed||!routes.Contains(route))return;
    route.GeoCode=result.Code;route.GeoName=result.Country;route.GeoCheckedAt=result.Checked;route.GeoRegion=result.Region;route.GeoCity=result.City;route.GeoVersion=result.Version;
    ImageSource image=null;
    if(result.Flag!=null)try{var bitmap=new BitmapImage();bitmap.BeginInit();bitmap.CacheOption=BitmapCacheOption.OnLoad;bitmap.StreamSource=new MemoryStream(result.Flag);bitmap.EndInit();bitmap.Freeze();image=bitmap;}catch{}
    route.UpdateProbe(result.Latency,result.Ping,result.Reachable,image);
   }catch{if(!closed&&routes.Contains(route))route.UpdateProbe("检测未完成","可再次测延迟",false,null);}
   finally{probeLimit.Release();}
  }
  async Task ProbeRoutes(bool force=false) {
   if(probing||closed)return;probing=true;Button("ProbeButton").IsEnabled=false;Button("ProbeButton").Content="检测中…";
   int targetPort=ConfiguredProbePort();Text("ProbeHint").Text=targetPort==0?"frps 端口配置无效 · 未进行 TCP 测速":"本机 → VPS · TCP "+targetPort+" / ICMP Ping";
   if(force)foreach(var route in routes)if(String.IsNullOrEmpty(route.CountryCode)&&String.IsNullOrEmpty(route.GeoCode))route.GeoCheckedAt=null;
   try {await Task.WhenAll(routes.ToList().Select(r=>ProbeOne(r)));if(!closed)SaveRoutes();}
   catch(Exception ex){if(!closed)History("检测结果保存失败："+ex.Message);}
   finally{probing=false;lastProbe=DateTime.UtcNow;if(!closed){Button("ProbeButton").IsEnabled=true;Button("ProbeButton").Content="测延迟";}}
  }
  Dictionary<string,object> RunBackend(string action,string address) {
   var start=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"System32","WindowsPowerShell","v1.0","powershell.exe"));
   start.Arguments="-NoProfile -ExecutionPolicy Bypass -File "+Q(backend)+" -ProjectRoot "+Q(root)+" -StartScript "+Q(startScript)+" -BackendAction "+action+(String.IsNullOrEmpty(address)?"":" -ServerAddress "+Q(address));
   start.UseShellExecute=false;start.CreateNoWindow=true;start.WorkingDirectory=root;
   start.RedirectStandardOutput=true;start.RedirectStandardError=true;start.StandardOutputEncoding=new UTF8Encoding(false);start.StandardErrorEncoding=new UTF8Encoding(false);
   using(var process=new Process{StartInfo=start}) {
    process.Start();var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
     // Start may need the initial validation, startup timeout and a bounded rollback.
     int timeoutMs=action=="Start"?180000:130000;
     if(!process.WaitForExit(timeoutMs)){try{process.Kill();}catch{}throw new TimeoutException("操作等待超时。已有服务可能仍在运行，请先刷新状态，不要连续重试。");}
    process.WaitForExit();string text=output.Result.Trim().TrimStart('\uFEFF');
    if(String.IsNullOrEmpty(text))throw new InvalidOperationException("后台没有返回状态，请检查项目脚本与 Windows PowerShell。");
    try {return new JavaScriptSerializer{MaxJsonLength=2097152}.Deserialize<Dictionary<string,object>>(text);} catch {throw new InvalidDataException("后台返回格式异常，请检查服务脚本；本次没有自动重复操作。");}
   }
  }
  async Task<Dictionary<string,object>> Request(string action,string address) {
   await gate.WaitAsync();try{return await Task.Run(()=>RunBackend(action,address));}finally{gate.Release();}
  }
  async Task Refresh() {
   if(busy||checking||closed)return;checking=true;UpdateButtons();
   try {var result=await Request("Status",null);if(!closed&&!busy){if(D(result,"Status")!=null)Apply(D(result,"Status"));if(!B(result,"Success")){actionError=S(result,"Message");Banner(actionError,"#FCE8E8");}}}
   catch(Exception ex){if(!closed&&!busy){actionError=ex.Message;Banner(actionError,"#FCE8E8");}}
   finally{checking=false;if(!closed)UpdateButtons();}
  }
  async Task Action(string action) {
   if(busy||closed)return;
   string address=Selected==null?null:Selected.Address;
   if(action=="Start"&&address==null)return;
   busy=true;actionError="";UpdateButtons();Control<ProgressBar>("Progress").Visibility=Visibility.Visible;
   Banner(action=="Start"?"正在准备所选线路与服务，请稍候…":action=="StopAll"?"正在停止模型 API 与本项目隧道…":action=="Stop"?"正在停止本项目隧道…":action=="StopOllama"?"正在停止已确认归属的模型监听…":"正在打开 Cherry Studio…","#E5EDFA");History("开始操作："+action);
   try {
    var result=await Request(action,address);
    if(D(result,"Status")!=null)Apply(D(result,"Status"));
    string message=S(result,"Message");History(message);
    if(!B(result,"Success")){actionError=message;Banner(message,"#FCE8E8");}
    else if(action=="Stop"||action=="StopOllama"||action=="StopAll")Banner(message,"#E5EDFA");
   }catch(Exception ex){actionError=ex.Message;Banner(actionError,"#FCE8E8");History(actionError);}
   finally{busy=false;Control<ProgressBar>("Progress").Visibility=Visibility.Collapsed;UpdateButtons();}
  }
  void Apply(Dictionary<string,object> value) {
   state=value;string green="#168565",gray="#7A889D",amber="#B47719";
   bool online=B(value,"OllamaOnline"),ts=B(value,"TailscaleConnected");var tunnel=D(value,"Tunnel");
   bool ready=S(tunnel,"State")=="ready";int count=Convert.ToInt32(value["FrpcCount"]);
   Text("OllamaState").Text=online?"在线":B(value,"OllamaListening")?"待检查":"未启动";Text("OllamaState").Foreground=Brush("#243B53");Control<System.Windows.Shapes.Ellipse>("OllamaDot").Fill=Brush(online?green:B(value,"OllamaListening")?amber:gray);
   object reported;int reportedPort=value.TryGetValue("OllamaPort",out reported)?Convert.ToInt32(reported):ModelPort;
   Text("OllamaDetail").Text="127.0.0.1:"+reportedPort+(online&&!B(value,"ModelInstalled")?" · 默认模型未安装":"");
   Text("TunnelState").Text=S(tunnel,"Text");Text("TunnelState").Foreground=Brush("#243B53");Control<System.Windows.Shapes.Ellipse>("TunnelDot").Fill=Brush(ready?green:count>0?amber:gray);Text("TunnelDetail").Text=S(tunnel,"Detail");
   Text("TsState").Text=ts?"已连接":"未连接";Text("TsState").Foreground=Brush("#243B53");Control<System.Windows.Shapes.Ellipse>("TsDot").Fill=Brush(ts?green:gray);Text("TsDetail").Text=ts?S(value,"TailscaleIP")+" · 仅网络状态":"可选辅助连接 · frp 可以独立工作";
   var current=routes.FirstOrDefault(r=>SameAddress(r.Address,S(value,"Server")));Text("CurrentRoute").Text="已保存："+(current==null?S(value,"Server"):current.Name);
   Text("ModelText").Text="默认模型  "+S(value,"Model")+"  ▾";Text("CheckedAt").Text="最近检查 "+DateTime.Now.ToString("HH:mm:ss")+" · 15 秒刷新 · 关窗保留服务";
   if(!routeInitialized){routeInitialized=true;List.SelectedItem=current??routes.FirstOrDefault();}
   Text("HeaderState").Text=online&&ready?(B(value,"ModelInstalled")?"模型与隧道已就绪":"服务在线 · 默认模型未安装"):online?(count>0?"Ollama 在线 · 隧道待就绪":"Ollama 在线 · 隧道未启动"):ready?"隧道已注册 · Ollama 离线":count>0?"隧道连接中 · Ollama 离线":"等待启动";
   if(!online&&B(value,"OllamaListening"))Text("HeaderState").Text="模型端口待检查";
   Control<System.Windows.Shapes.Ellipse>("HeaderDot").Fill=Brush(online&&ready?green:AnyServiceRunning?amber:gray);
   UpdateStatusBanner();
   UpdateButtons();
  }
  void UpdateStatusBanner() {
   if(state==null||!String.IsNullOrEmpty(actionError))return;
   string server=S(state,"Server");var current=routes.FirstOrDefault(r=>SameAddress(r.Address,server));string currentName=current==null?server:current.Name;
   if(Selected!=null&&!SameAddress(Selected.Address,server)) {
    Banner("当前线路："+currentName+"（已保存配置）。已选择 "+Selected.Name+"，尚未应用；点击“应用并切线”后生效。","#E5EDFA");return;
   }
   bool online=B(state,"OllamaOnline"),ready=S(D(state,"Tunnel"),"State")=="ready",installed=B(state,"ModelInstalled");int count=Convert.ToInt32(state["FrpcCount"]);
   if(online&&ready) {
    if(installed)Banner("Ollama API 在线，默认模型已安装；当前线路 "+currentName+" 的隧道已注册。通过 frp 连接的远端设备仍需运行同一线路的 visitor。","#E0F3EB");
    else Banner("Ollama API 在线，当前线路 "+currentName+" 的隧道已注册；默认模型 "+S(state,"Model")+" 尚未安装。请使用已安装模型或更新默认模型设置。","#FFF0D8");
   }else if(ready)Banner("当前线路 "+currentName+" 的隧道已注册，模型尚未响应。可点“补齐启动”准备模型，或停止当前服务。","#FFF0D8");
   else if(count>0)Banner("当前线路 "+currentName+" 的隧道进程已运行，等待注册或需要检查；进程运行不代表远端已连通。"+(online&&!installed?"默认模型尚未安装。":""),"#FFF0D8");
   else if(B(state,"OllamaListening")&&!online)Banner("模型端口已有监听，但 API 尚未响应；请核对原模型程序，避免重复启动。","#FFF0D8");
   else if(online)Banner("模型 API 在线，远程隧道尚未启动。点“补齐启动”建立连接，或一键停止服务。"+(!installed?"默认模型尚未安装。":""),"#E5EDFA");
   else Banner("选择一条线路，点击“一键启动”，准备模型与远程连接。","#E5EDFA");
  }
  bool AnyServiceRunning {get{return state!=null&&(B(state,"OllamaOnline")||B(state,"OllamaListening")||Convert.ToInt32(state["FrpcCount"])>0);}}
  bool PendingRoute {get{return state!=null&&Selected!=null&&!SameAddress(Selected.Address,S(state,"Server"));}}
  string PrimaryActionName {get{return PendingRoute?"Start":AnyServiceRunning?"StopAll":"Start";}}
  void UpdateButtons() {
   foreach(string name in new[]{"StartButton","StopButton","CherryButton","RefreshButton","AddRouteButton","EditRouteButton","DeleteRouteButton","StopOllamaButton","SettingsButton","WebButton","ModelButton","OllamaSettingsButton","FrpSettingsButton","TailscaleInfoButton","ResumeButton","GlobalStopButton"})Button(name).IsEnabled=!busy;
   List.IsEnabled=!busy;Button("StartButton").IsEnabled=!busy&&state!=null&&(AnyServiceRunning||Selected!=null);
   bool tunnelRunning=state!=null&&Convert.ToInt32(state["FrpcCount"])>0;
   Button("StopButton").IsEnabled=!busy&&tunnelRunning;Button("StopButton").Visibility=tunnelRunning?Visibility.Visible:Visibility.Hidden;
   Button("StopOllamaButton").IsEnabled=!busy&&state!=null&&B(state,"OllamaCanStop");
   Button("StopOllamaButton").Visibility=state!=null&&(B(state,"OllamaOnline")||B(state,"OllamaListening")||B(state,"OllamaCanStop"))?Visibility.Visible:Visibility.Hidden;
   Button("StopOllamaButton").ToolTip=state==null?"正在确认模型监听归属":S(state,"OllamaStopReason");
   Button("RefreshButton").IsEnabled=!busy&&!checking;Button("EditRouteButton").IsEnabled=!busy&&Selected!=null;Button("DeleteRouteButton").IsEnabled=!busy&&Selected!=null;
   bool stop=PrimaryActionName=="StopAll";
   Button("StartButton").Content=PendingRoute?"应用并切线":stop?"一键停止":Selected==null?"先选择线路":"一键启动";
   Button("StartButton").Background=Brush(stop?"#30465F":"#3168E8");Button("StartButton").BorderBrush=Button("StartButton").Background;
   Button("StartButton").ToolTip=stop?"停止本项目隧道和当前端口已确认的模型服务":"准备模型与所选线路";
   System.Windows.Automation.AutomationProperties.SetName(Button("StartButton"),stop?"一键停止模型与隧道":PendingRoute?"应用并切线":"一键启动模型与隧道");
   Button("GlobalStopButton").Visibility=AnyServiceRunning&&PendingRoute?Visibility.Visible:Visibility.Collapsed;
   Button("ResumeButton").Visibility=AnyServiceRunning&&!PendingRoute&&Selected!=null&&!(B(state,"OllamaOnline")&&tunnelRunning)?Visibility.Visible:Visibility.Collapsed;
  }
  void Banner(string text,string color){Text("BannerText").Text=text;Control<Border>("Banner").Background=Brush(color);}
  void History(string text){if(String.IsNullOrEmpty(text))return;var box=Control<TextBox>("History");box.AppendText("["+DateTime.Now.ToString("HH:mm:ss")+"] "+text+Environment.NewLine);box.ScrollToEnd();}
 }
 public sealed class RouteDialog:Window {
  readonly TextBox name,address,region,location;
  readonly ComboBox country;readonly Route previous;
  sealed class Choice {public string Code{get;set;}public string Name{get;set;}}
  public Route Result {get;private set;}
  public RouteDialog(Route old) {
   previous=old;Title=old==null?"添加 VPS 线路":"编辑 VPS 线路";Width=460;Height=480;ResizeMode=ResizeMode.NoResize;WindowStartupLocation=WindowStartupLocation.CenterOwner;
   FontFamily=new FontFamily("Microsoft YaHei UI");FontSize=13;Background=new SolidColorBrush(Color.FromRgb(242,245,250));
   var body=new StackPanel{Margin=new Thickness(24)};Content=body;
   name=Field(body,"线路名称",old==null?"":old.Name);address=Field(body,"服务器 IP / 域名（不含端口）",old==null?"":old.Address);region=Field(body,"备注（例如 国内 / 海外，可留空）",old==null?"":old.Region);
   body.Children.Add(new TextBlock{Text="国家 / 地区（自动结果可在此修正）",Margin=new Thickness(0,0,0,5)});
   country=new ComboBox{Height=30,DisplayMemberPath="Name",SelectedValuePath="Code"};body.Children.Add(country);
   var options=new List<Choice>{new Choice{Code="",Name="自动识别（IP 地理信息）"}};
   foreach(var code in CultureInfo.GetCultures(CultureTypes.SpecificCultures).Select(c=>{try{return new RegionInfo(c.Name).TwoLetterISORegionName;}catch{return "";}}).Where(c=>c.Length==2).Distinct().OrderBy(c=>CountryNames.Name(c,null)))options.Add(new Choice{Code=code,Name=CountryNames.Name(code,null)});
   country.ItemsSource=options;country.SelectedValue=old==null||String.IsNullOrEmpty(old.CountryCode)?"":old.CountryCode;
   body.Children.Add(new TextBlock{Text="",Height=8});location=Field(body,"省 / 州、城市（留空自动识别）",old==null?"":old.Location);
   var buttons=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right,Margin=new Thickness(0,12,0,0)};body.Children.Add(buttons);
   var cancel=new Button{Content="取消",Width=80,Height=34,Margin=new Thickness(0,0,8,0),IsCancel=true};buttons.Children.Add(cancel);
   var save=new Button{Content="保存线路",Width=100,Height=34,IsDefault=true,Background=new SolidColorBrush(Color.FromRgb(37,99,235)),Foreground=Brushes.White};buttons.Children.Add(save);
   save.Click+=(s,e)=>{Result=new Route{Name=name.Text.Trim(),Address=address.Text.Trim(),Region=region.Text.Trim(),CountryCode=Convert.ToString(country.SelectedValue),Location=location.Text.Trim()};if(previous!=null&&String.Equals(previous.Address,Result.Address,StringComparison.OrdinalIgnoreCase)){Result.GeoCode=previous.GeoCode;Result.GeoName=previous.GeoName;Result.GeoCheckedAt=previous.GeoCheckedAt;Result.GeoRegion=previous.GeoRegion;Result.GeoCity=previous.GeoCity;Result.GeoVersion=previous.GeoVersion;}try{Panel.ValidateRoute(Result);DialogResult=true;}catch(Exception ex){MessageBox.Show(this,ex.Message,"请检查输入",MessageBoxButton.OK,MessageBoxImage.Information);}};
   Loaded+=(s,e)=>name.Focus();
  }
  static TextBox Field(StackPanel parent,string label,string value) {
   parent.Children.Add(new TextBlock{Text=label,Margin=new Thickness(0,0,0,5),Foreground=new SolidColorBrush(Color.FromRgb(70,90,120))});
   var box=new TextBox{Text=value??"",Height=30,Padding=new Thickness(7,4,7,4),Margin=new Thickness(0,0,0,10)};parent.Children.Add(box);return box;
  }
 }
}
