using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace LocalAIControl {
 public sealed class PanelOperationLock : IDisposable {
  Mutex mutex;bool held;
  public PanelOperationLock() {
   mutex=new Mutex(false,"Local\\LocalAIControl-"+Environment.UserName);
   try{try{held=mutex.WaitOne(0);}catch(AbandonedMutexException){held=true;}if(!held)throw new IOException("服务操作正在进行，请完成后再保存设置。");}
   catch{mutex.Dispose();mutex=null;throw;}
  }
  public void Dispose(){if(mutex==null)return;try{if(held)mutex.ReleaseMutex();}finally{mutex.Dispose();mutex=null;held=false;}}
 }
 public static class PanelPreferences {
  public static Dictionary<string,object> Read(string path) {
   return ReadSnapshot(Bytes(path));
  }
  public static Dictionary<string,object> ReadSnapshot(byte[] bytes) {
   if(bytes==null)return new Dictionary<string,object>();
   var result=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(new UTF8Encoding(false,true).GetString(bytes).TrimStart('\uFEFF'));
   if(result==null)throw new InvalidDataException("面板设置无法读取，原文件已保留。");return result;
  }
  public static int Port(Dictionary<string,object> values,string key,int fallback) {
   object raw;if(!values.TryGetValue(key,out raw))return fallback;
   int port;if(!(raw is int)||!Int32.TryParse(Convert.ToString(raw),out port)||port<1||port>65535)throw new InvalidDataException(key+" 需为 1–65535 的整数。");return port;
  }
  public static string WebUrl(string value) {
   if(String.IsNullOrWhiteSpace(value))return "";
   Uri uri;if(!Uri.TryCreate(value.Trim(),UriKind.Absolute,out uri)||(uri.Scheme!="http"&&uri.Scheme!="https")||uri.Port<1||!String.IsNullOrEmpty(uri.UserInfo)||value.Any(Char.IsControl))throw new ArgumentException("网页地址需为完整 HTTP/HTTPS 地址，不能包含用户名或密码。");
   return uri.AbsoluteUri;
  }
  public static byte[] Bytes(string path){return File.Exists(path)?File.ReadAllBytes(path):null;}
  public static string Hash(byte[] bytes){if(bytes==null)return "missing";using(var sha=SHA256.Create())return Convert.ToBase64String(sha.ComputeHash(bytes));}
  public static void RecoverPorts(string root) {
   string folder=Path.Combine(root,"config"),pending=Path.Combine(folder,".panel-port-change.pending.json");if(!File.Exists(pending))return;
   var plan=Read(pending);string config=Path.Combine(folder,"frpc.toml"),prefs=Path.Combine(folder,"panel-preferences.json");
   byte[] configBytes=Bytes(config),prefsBytes=Bytes(prefs);string ch=Hash(configBytes),ph=Hash(prefsBytes);Func<string,string> expected=key=>plan.ContainsKey(key)?Convert.ToString(plan[key]):"invalid";
   if((ch==expected("configBefore")&&ph==expected("prefsBefore"))||(ch==expected("configAfter")&&ph==expected("prefsAfter"))){File.Delete(pending);return;}
   byte[] backup=Bytes(config+".port-previous.bak");
   if(ch==expected("configAfter")&&ph==expected("prefsBefore")&&Hash(backup)==expected("configBefore")){Write(config,configBytes,backup,null);File.Delete(pending);return;}
   throw new IOException("检测到未完成的端口保存与外部变化，请保留 .port-previous.bak 和 pending 记录，核对后再启动。");
  }
  public static bool Same(byte[] a,byte[] b){return a==null?b==null:b!=null&&a.SequenceEqual(b);}
  public static void Write(string path,byte[] expected,byte[] data,string backup) {
   if(!Same(Bytes(path),expected))throw new IOException("配置已被其他程序修改，请重新打开设置。");
   if(Same(expected,data))return;
   string temp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
   try {File.WriteAllBytes(temp,data);if(!Same(Bytes(path),expected))throw new IOException("配置在保存时发生变化，请重试。");if(expected==null)File.Move(temp,path);else File.Replace(temp,path,backup);}
   finally{if(File.Exists(temp))File.Delete(temp);}
  }
 }
 public static class ModelChoice {
  public static bool Valid(string name){return !String.IsNullOrWhiteSpace(name)&&name.Length<=512&&name.Trim()==name&&!name.Any(Char.IsControl)&&!name.Any(Char.IsWhiteSpace);}
  public static string[] Available(Dictionary<string,object> status){
   object raw;if(status==null||!status.TryGetValue("Models",out raw)||raw is string)return new string[0];
   var list=raw as System.Collections.IEnumerable;if(list==null)return new string[0];
   var names=new HashSet<string>(StringComparer.Ordinal);foreach(object item in list){var name=item as string;if(Valid(name))names.Add(name);}
   return names.OrderBy(x=>x,StringComparer.OrdinalIgnoreCase).ToArray();
  }
  public static string Current(byte[] bytes){var values=PanelPreferences.ReadSnapshot(bytes);object raw;return values.TryGetValue("defaultModel",out raw)&&raw is string?(string)raw:"";}
  public static string Resolve(string name,IEnumerable<string> installed){
   if(!Valid(name)||installed==null)return null;if(installed.Contains(name,StringComparer.Ordinal))return name;
   return name.LastIndexOf(':')<=name.LastIndexOf('/')&&installed.Contains(name+":latest",StringComparer.Ordinal)?name+":latest":null;
  }
  public static void Save(string path,byte[] expected,string name,IEnumerable<string> installed){
   if(expected==null)throw new IOException("模型设置文件缺失，未创建或覆盖；请先恢复项目设置。");
   if(!Valid(name)||installed==null||!installed.Contains(name,StringComparer.Ordinal))throw new InvalidOperationException("所选模型不在当前已安装列表，请刷新后重新选择。");
   var values=PanelPreferences.ReadSnapshot(expected);object old;
   if(values.TryGetValue("defaultModel",out old)&&old is string&&(string)old==name){if(!PanelPreferences.Same(expected,PanelPreferences.Bytes(path)))throw new IOException("模型设置已被修改，请重新打开。");return;}
   values["defaultModel"]=name;PanelPreferences.Write(path,expected,new UTF8Encoding(false).GetBytes(new JavaScriptSerializer().Serialize(values)),path+".previous");
  }
 }
 public sealed partial class Panel {
  bool openingWeb;
  int ModelPort {get{return PanelPreferences.Port(PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json")),"ollamaPort",11434);}}
  int VisitorPort {get{return PanelPreferences.Port(PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json")),"visitorPort",11436);}}
  string ModelUrl {get{return "http://127.0.0.1:"+ModelPort;}}
  Window SmallWindow(string title,int height=420) {
   var dialog=new Window{Title=title,Owner=Window,Width=600,Height=height,MinWidth=500,MinHeight=320,WindowStartupLocation=WindowStartupLocation.CenterOwner,Background=Brush("#F6F8FC"),FontFamily=Window.FontFamily,FontSize=13};
   dialog.Resources=Window.Resources;dialog.PreviewKeyDown+=(s,e)=>{if(e.Key==System.Windows.Input.Key.Escape&&!busy){dialog.Close();e.Handled=true;}};return dialog;
  }
  Button SmallButton(string label){return new Button{Content=label,Padding=new Thickness(14,6,14,6),MinHeight=32,Margin=new Thickness(0,0,8,0)};}
  TextBlock Paragraph(string text,bool bold=false){return new TextBlock{Text=text,TextWrapping=TextWrapping.Wrap,Foreground=Brush("#4C6280"),FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,Margin=new Thickness(0,0,0,12)};}
  void ConnectionWindow() {
   var dialog=SmallWindow("连接信息",415);var body=new StackPanel{Margin=new Thickness(24)};
   body.Children.Add(Paragraph("这台 Windows",true));body.Children.Add(Paragraph(ModelUrl+"  ·  Ollama\n"+ModelUrl+"/v1  ·  OpenAI 兼容"));
   var copy=SmallButton("复制本机 OpenAI 地址");copy.Click+=(s,e)=>{try{Clipboard.SetText(ModelUrl+"/v1");copy.Content="已复制";}catch{copy.Content="剪贴板暂不可用";}};body.Children.Add(copy);
   body.Children.Add(new Border{Height=20});body.Children.Add(Paragraph("Mac / 手机 / 其他接收设备",true));body.Children.Add(Paragraph("从线路右侧复制 visitor TOML，放到该设备的 frpc 中运行。客户端填该设备自己的回环入口；默认 http://127.0.0.1:"+VisitorPort+"，以复制配置的 bindPort 为准。\n127.0.0.1 指当前设备，不能直接当成 Windows 的公网地址。"));
   body.Children.Add(Paragraph(state==null?"默认模型尚未读取":"项目默认模型："+S(state,"Model")));dialog.Content=new ScrollViewer{Content=body,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ShowDialog();
  }
  void HistoryWindow() {
   var dialog=SmallWindow("日志与操作记录",460);var grid=new DockPanel{Margin=new Thickness(20)};var bar=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,0,0,12)};
   var folder=SmallButton("打开日志目录");folder.Click+=(s,e)=>{try{string path=Path.Combine(root,"logs");Directory.CreateDirectory(path);Process.Start("explorer.exe",Q(path));}catch(Exception ex){MessageBox.Show(dialog,ex.Message,"未能打开目录");}};
   var refresh=SmallButton("刷新记录");bar.Children.Add(folder);bar.Children.Add(refresh);DockPanel.SetDock(bar,Dock.Top);grid.Children.Add(bar);
   var text=new TextBox{IsReadOnly=true,TextWrapping=TextWrapping.Wrap,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,Padding=new Thickness(12),Text=Control<TextBox>("History").Text};refresh.Click+=(s,e)=>{text.Text=Control<TextBox>("History").Text;text.ScrollToEnd();};grid.Children.Add(text);dialog.Content=grid;dialog.ShowDialog();
  }
  void ExportRoute(Route route) {
   if(busy||route==null)return;
   try {
    ValidateRoute(route);int bindPort=VisitorPort;string toml="# 线路："+String.Concat(route.Name.Select(c=>Char.IsControl(c)?' ':c))+Environment.NewLine+VisitorExport.Build(File.ReadAllText(Path.Combine(root,"config","frpc.toml"),Encoding.UTF8),route.Address,bindPort);
    Clipboard.SetText(toml,TextDataFormat.UnicodeText);
    History("已复制接收设备 visitor TOML；含私密连接密钥，请私下保管。未切换当前线路。");Banner("已复制“"+route.Name+"”的 TOML，接收端口 "+bindPort+"。含连接密钥，请私下粘贴；未切线。","#E0F3EB");
   }catch(System.Runtime.InteropServices.ExternalException){Banner("剪贴板正被其他程序占用，请稍后再点“复制配置”。未生成文件或切换线路。","#FFF0D8");}
   catch(Exception){Banner("复制未完成：当前需为可支持的单行 STCP/token 配置。复杂或动态凭据配置请在原 frpc 工具中处理。","#FFF0D8");}
  }
  async Task OpenWeb() {
   if(busy||openingWeb)return;openingWeb=true;Button("WebButton").IsEnabled=false;
   try {
    var pref=PanelPreferences.Read(Path.Combine(root,"config","panel-preferences.json"));object raw;string url=pref.TryGetValue("webChatUrl",out raw)?PanelPreferences.WebUrl(Convert.ToString(raw)):"";
    if(String.IsNullOrEmpty(url)) {
     bool shared=false;try{shared=await Task.Run(()=>{var request=(HttpWebRequest)WebRequest.Create("http://127.0.0.1:18765/api/session");request.Proxy=null;request.Timeout=1200;request.ReadWriteTimeout=1200;request.AllowAutoRedirect=false;using(var response=request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream())){char[] chars=new char[8193];int count=reader.ReadBlock(chars,0,chars.Length);if(count>8192)return false;var identity=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(new string(chars,0,count));return S(identity,"service")=="local-ai-console";}});}catch{}
     if(shared)url="http://127.0.0.1:18765/chat";
     else {SettingsWindow("先填写已部署的网页聊天地址，例如本机 Open WebUI；Ollama API 本身不提供聊天网页。");return;}
    }
    Process.Start(new ProcessStartInfo(url){UseShellExecute=true});History("已请求打开配置的网页版。页面是否可用以浏览器实际连接为准。");
   }catch(Exception ex){Banner(ex.Message,"#FFF0D8");}finally{openingWeb=false;if(!closed)Button("WebButton").IsEnabled=!busy;}
  }
  void TailscaleWindow() {
   if(busy)return;var dialog=SmallWindow("Tailscale · 连接信息",350);var body=new StackPanel{Margin=new Thickness(24)};
   string ip=state==null?"":S(state,"TailscaleIP");body.Children.Add(Paragraph(String.IsNullOrEmpty(ip)?"当前没有可读取的 Tailscale IPv4 地址。":"本机 Tailscale IP："+ip,true));
   body.Children.Add(Paragraph("IP 由 Tailscale 分配，连接与账号由系统 Tailscale 客户端管理。此处显示网络状态；模型是否能经此地址访问，还取决于模型监听与网络配置。"));
   var copy=SmallButton("复制 IP");copy.IsEnabled=!String.IsNullOrEmpty(ip);copy.Click+=(s,e)=>{try{Clipboard.SetText(ip);copy.Content="已复制";}catch{copy.Content="剪贴板暂不可用";}};body.Children.Add(copy);
   dialog.Content=new ScrollViewer{Content=body,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ShowDialog();
  }
  void ModelWindow() {
   if(busy)return;string path=Path.Combine(root,"config","model-settings.json");byte[] snapshot;string current;
   try{snapshot=PanelPreferences.Bytes(path);if(snapshot==null)throw new IOException();current=ModelChoice.Current(snapshot);}catch{Banner("模型设置无法读取，原文件已保留；请检查 model-settings.json。","#FFF0D8");return;}
   var dialog=SmallWindow("选择项目默认模型",345);var content=new StackPanel{Margin=new Thickness(24)};
   content.Children.Add(Paragraph("默认模型",true));content.Children.Add(Paragraph("只修改本项目的默认选择。不会下载或加载模型，也不会停止当前问答；聊天客户端仍使用自己选定的模型。"));
   var currentText=Paragraph("当前："+(String.IsNullOrEmpty(current)?"尚未设置":current));content.Children.Add(currentText);
   var list=new ComboBox{MinHeight=36,Margin=new Thickness(0,0,0,14),IsEditable=false,MaxDropDownHeight=220};System.Windows.Automation.AutomationProperties.SetName(list,"本机已安装模型");content.Children.Add(list);
   var status=Paragraph("正在读取已安装模型…");content.Children.Add(status);var row=new StackPanel{Orientation=Orientation.Horizontal};var refresh=SmallButton("刷新列表");var save=SmallButton("保存默认模型");save.Background=Brush("#3677F6");save.BorderBrush=Brush("#3677F6");save.Foreground=Brushes.White;save.IsEnabled=false;var close=SmallButton("取消");row.Children.Add(refresh);row.Children.Add(save);row.Children.Add(close);content.Children.Add(row);
   bool loading=false,dialogClosed=false,saving=false;int catalogPort=0;string[] installed=new string[0];
   Func<Task> load=async()=>{
    if(loading||saving||dialogClosed)return;loading=true;save.IsEnabled=false;refresh.IsEnabled=false;list.IsEnabled=false;
    try{int port=ModelPort;var result=await Request("Status",null);if(dialogClosed)return;var actual=D(result,"Status");
     if(!B(result,"Success")||actual==null||!B(actual,"OllamaOnline"))throw new IOException("无法读取已安装列表。请先启动模型服务再刷新；若端口有误，关闭此窗后从 Ollama 卡片修改端口。");
     if(Convert.ToInt32(actual["OllamaPort"])!=port||ModelPort!=port)throw new IOException("模型端口已变化，请重新打开模型选择。");
     installed=ModelChoice.Available(actual);catalogPort=port;string selected=list.SelectedItem as string,matched=ModelChoice.Resolve(current,installed);list.ItemsSource=installed;list.SelectedItem=installed.Contains(selected)?selected:matched;
     status.Text=installed.Length==0?"服务在线，当前没有已安装模型。请在模型工具中安装后刷新。":installed.Length+" 个已安装模型 · API 127.0.0.1:"+port+(matched!=null?"":"。当前默认模型未安装，请选择一个。");
    }catch(Exception ex){installed=new string[0];list.ItemsSource=installed;status.Text=ex.Message;}finally{loading=false;if(!dialogClosed){refresh.IsEnabled=true;list.IsEnabled=true;save.IsEnabled=installed.Length>0&&list.SelectedItem!=null;}}
   };
   refresh.Click+=async(s,e)=>await load();list.SelectionChanged+=(s,e)=>{if(!loading&&!saving)save.IsEnabled=list.SelectedItem!=null&&installed.Length>0;};close.Click+=(s,e)=>dialog.Close();
   save.Click+=async(s,e)=>{
    if(loading||saving)return;string selected=list.SelectedItem as string;if(selected==null)return;IDisposable operation=null;
    try{saving=true;busy=true;UpdateButtons();save.IsEnabled=false;refresh.IsEnabled=false;list.IsEnabled=false;close.IsEnabled=false;operation=new PanelOperationLock();
     if(ModelPort!=catalogPort)throw new IOException("模型端口已变化，请重新打开模型选择。");
     var result=await Request("Status",null);var actual=D(result,"Status");if(!B(result,"Success")||actual==null||!B(actual,"OllamaOnline")||Convert.ToInt32(actual["OllamaPort"])!=catalogPort||ModelPort!=catalogPort)throw new IOException("未能重新确认当前模型列表，设置未保存。请刷新后重试。");
     ModelChoice.Save(path,snapshot,selected,ModelChoice.Available(actual));History("项目默认模型已设为 "+selected+"；未发起推理。");actionError="";busy=false;saving=false;dialog.Close();
    }catch(Exception ex){if(!dialogClosed)status.Text=ex.Message;}finally{if(operation!=null)operation.Dispose();busy=false;saving=false;UpdateButtons();if(!dialogClosed){save.IsEnabled=list.SelectedItem!=null;refresh.IsEnabled=true;list.IsEnabled=true;close.IsEnabled=true;}}
    if(dialogClosed&&!closed)await Refresh();
   };
   dialog.Closing+=(s,e)=>{if(saving)e.Cancel=true;};dialog.Closed+=(s,e)=>dialogClosed=true;dialog.Content=new ScrollViewer{Content=content,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ContentRendered+=async(s,e)=>await load();dialog.ShowDialog();
  }
  void SettingsWindow(string notice="",string focus="") {
   if(busy)return;
   string prefPath=Path.Combine(root,"config","panel-preferences.json"),configPath=Path.Combine(root,"config","frpc.toml");
   Dictionary<string,object> values;byte[] originalPref,originalConfig;int[] ports;int initialModel,initialVisitor;
   try{using(new PanelOperationLock()){PanelPreferences.RecoverPorts(root);originalPref=PanelPreferences.Bytes(prefPath);values=PanelPreferences.ReadSnapshot(originalPref);originalConfig=File.ReadAllBytes(configPath);ports=PortConfig.Read(new UTF8Encoding(false,true).GetString(originalConfig).TrimStart('\uFEFF'));initialModel=PanelPreferences.Port(values,"ollamaPort",11434);initialVisitor=PanelPreferences.Port(values,"visitorPort",11436);}}
   catch{Banner("设置无法读取：原配置已保留。请先检查 STCP 配置格式及面板偏好文件。","#FFF0D8");return;}
   var dialog=SmallWindow("设置 · 端口与客户端",565);dialog.ResizeMode=ResizeMode.CanResize;var content=new StackPanel{Margin=new Thickness(24)};
   content.Children.Add(Paragraph("连接与端口",true));content.Children.Add(Paragraph("端口修改会保存配置，不会偷偷重启服务。改模型/frps 端口前需停止本项目隧道；之后点击右上启动。"));
   Func<string,string,TextBox> field=(label,value)=>{content.Children.Add(new TextBlock{Text=label,Foreground=Brush("#526987"),Margin=new Thickness(0,0,0,5)});var box=new TextBox{Text=value,Padding=new Thickness(9,7,9,7),Margin=new Thickness(0,0,0,12)};content.Children.Add(box);return box;};
   var model=field("模型 API 端口（Windows 回环监听）",initialModel.ToString());
   var frps=field("frps 服务端端口（需与 VPS 上的 frps 一致）",ports[0].ToString());
   var visitor=field("接收设备本地端口（复制配置时使用）",initialVisitor.ToString());
   object current;var web=field("网页聊天地址（已部署的 Open WebUI / 共享 /chat 页面）",values.TryGetValue("webChatUrl",out current)?Convert.ToString(current):"");
   var status=Paragraph(String.IsNullOrEmpty(notice)?"网页地址和接收端口保存后立即生效。模型端口同步到本项目 frpc localPort，旧端口上的模型服务保留。":notice);content.Children.Add(status);
   var save=SmallButton("保存设置");save.Background=Brush("#3677F6");save.BorderBrush=Brush("#3677F6");save.Foreground=Brushes.White;var close=SmallButton("取消");var row=new StackPanel{Orientation=Orientation.Horizontal};row.Children.Add(save);row.Children.Add(close);content.Children.Add(row);close.Click+=(s,e)=>dialog.Close();
   save.Click+=async(s,e)=>{
    IDisposable operation=null;byte[] changed=null;bool configChanged=false,committed=false;string pending=Path.Combine(root,"config",".panel-port-change.pending.json");
    try {
     int mp,sp,vp;if(!Int32.TryParse(model.Text,out mp)||!Int32.TryParse(frps.Text,out sp)||!Int32.TryParse(visitor.Text,out vp)||mp<1||mp>65535||sp<1||sp>65535||vp<1||vp>65535)throw new ArgumentException("端口必须是 1–65535 的整数。");
     string url=PanelPreferences.WebUrl(web.Text);bool portChanged=ports[0]!=sp||ports[1]!=mp||PanelPreferences.Port(values,"ollamaPort",11434)!=mp;
     busy=true;UpdateButtons();save.IsEnabled=false;close.IsEnabled=false;operation=new PanelOperationLock();
     if(!PanelPreferences.Same(originalPref,PanelPreferences.Bytes(prefPath))||!PanelPreferences.Same(originalConfig,PanelPreferences.Bytes(configPath)))throw new IOException("配置已被其他程序修改，请关闭后重新打开设置。");
     values["ollamaPort"]=mp;values["visitorPort"]=vp;values["webChatUrl"]=url;
     byte[] prefsAfter=new UTF8Encoding(false).GetBytes(json.Serialize(values));
     if(portChanged){var actual=await Request("Status",null);var statusValue=D(actual,"Status");if(!B(actual,"Success")||statusValue==null)throw new IOException("未能确认隧道状态，端口未修改。");if(Convert.ToInt32(statusValue["FrpcCount"])>0)throw new IOException("请先关闭设置，点击 frp 卡片的停止，再修改端口。现有连接未中断。");
      byte[] proposed=new UTF8Encoding(false).GetBytes(PortConfig.Change(new UTF8Encoding(false,true).GetString(originalConfig),sp,mp));
      var plan=new Dictionary<string,object>{{"configBefore",PanelPreferences.Hash(originalConfig)},{"configAfter",PanelPreferences.Hash(proposed)},{"prefsBefore",PanelPreferences.Hash(originalPref)},{"prefsAfter",PanelPreferences.Hash(prefsAfter)}};
      PanelPreferences.Write(pending,null,new UTF8Encoding(false).GetBytes(json.Serialize(plan)),null);changed=PortConfig.Apply(configPath,originalConfig,sp,mp);configChanged=!PanelPreferences.Same(originalConfig,changed);}
     PanelPreferences.Write(prefPath,originalPref,prefsAfter,prefPath+".previous");committed=true;if(File.Exists(pending))File.Delete(pending);
     History("已保存端口与客户端设置。模型/frp 端口需点击启动使用；其他实例未停止。");actionError="";lastProbe=DateTime.MinValue;if(portChanged){state=null;Text("HeaderState").Text="端口已保存 · 等待启动";Text("OllamaState").Text="待检查";Text("OllamaDetail").Text="127.0.0.1:"+mp+" · 等待检查";Text("TunnelState").Text="待检查";}busy=false;dialog.Close();
     Banner(portChanged?"端口已保存，请点击右上启动。旧端口上的服务仍保留；远端设备需重新复制对应配置。":"设置已保存，网页地址与接收端口立即生效。","#E0F3EB");
    }catch(Exception ex){
     if(!committed&&configChanged){try{PanelPreferences.Write(configPath,changed,originalConfig,null);}catch{status.Text="保存中断且检测到外部变化，请核对配置后再启动。恢复点为 .port-previous.bak。";return;}}
     if(!committed&&File.Exists(pending)){try{PanelPreferences.RecoverPorts(root);}catch{status.Text="保存中断，原记录已保留；请重新打开设置核对。";return;}}
     status.Text=ex.Message;
    }finally{if(operation!=null)operation.Dispose();busy=false;save.IsEnabled=true;close.IsEnabled=true;UpdateButtons();}
    if(committed&&!closed)await Refresh();
   };
   dialog.ContentRendered+=(s,e)=>{if(focus=="model"){model.Focus();model.SelectAll();}else if(focus=="frps"){frps.Focus();frps.SelectAll();}};
   dialog.Closing+=(s,e)=>{if(busy)e.Cancel=true;};dialog.Content=new ScrollViewer{Content=content,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};dialog.ShowDialog();
  }
 }
}
