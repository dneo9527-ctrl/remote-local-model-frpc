using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace LocalAIControl {
 // Deliberately a conservative, single-line TOML subset, not a general TOML parser.
 // No files, environment variables, templates, includes, processes or network access.
 public static class VisitorExport {
  sealed class Document {
   public readonly Dictionary<string,object> Common=new Dictionary<string,object>(StringComparer.Ordinal);
   public readonly List<Dictionary<string,object>> Proxies=new List<Dictionary<string,object>>();
  }
  static InvalidOperationException Refuse(string reason) {
   // Never include configuration values or source lines in an error.
   return new InvalidOperationException("无法自动导出："+reason+"。原配置未修改，请按实际配置手动准备 visitor。");
  }
  static string Key(string value) {
   if(!Regex.IsMatch(value,@"^[A-Za-z0-9_-]+(?:\s*\.\s*[A-Za-z0-9_-]+)*$"))throw Refuse("只支持普通未加引号的 TOML 键");
   return Regex.Replace(value,@"\s*\.\s*",".");
  }
  static string WithoutComment(string line) {
   char quote='\0';bool escape=false;
   for(int i=0;i<line.Length;i++) {
    char c=line[i];
    if(quote=='"'&&escape){escape=false;continue;}
    if(quote=='"'&&c=='\\'){escape=true;continue;}
    if(quote!='\0'){if(c==quote)quote='\0';continue;}
    if(c=='#')return line.Substring(0,i);
    if(c=='"'||c=='\'')quote=c;
   }
   if(quote!='\0'||escape)throw Refuse("不支持跨行字符串或未闭合引号");
   return line;
  }
  static string ReadString(string raw,ref int position) {
   char quote=raw[position++];var output=new StringBuilder();
   if(position+1<raw.Length&&raw[position]==quote&&raw[position+1]==quote)throw Refuse("不支持多行字符串");
   while(position<raw.Length) {
    char c=raw[position++];
    if(c==quote) {
     string value=output.ToString();
     if(value.IndexOf("{{",StringComparison.Ordinal)>=0||value.IndexOf("}}",StringComparison.Ordinal)>=0||value.IndexOf("${",StringComparison.Ordinal)>=0)throw Refuse("不支持动态环境变量或模板值");
     for(int j=0;j<value.Length;j++) {
      if(Char.IsHighSurrogate(value[j])){if(j+1>=value.Length||!Char.IsLowSurrogate(value[++j]))throw Refuse("字符串含无效 Unicode");}
      else if(Char.IsLowSurrogate(value[j]))throw Refuse("字符串含无效 Unicode");
     }
     return value;
    }
    if(c<0x20||c==0x7f)throw Refuse("不支持字符串中的原始控制字符");
    if(quote=='\''||c!='\\'){output.Append(c);continue;}
    if(position>=raw.Length)throw Refuse("字符串转义不完整");
    char escaped=raw[position++];
    switch(escaped) {
     case '"':output.Append('"');break;
     case '\\':output.Append('\\');break;
     case 'b':output.Append('\b');break;
     case 't':output.Append('\t');break;
     case 'n':output.Append('\n');break;
     case 'f':output.Append('\f');break;
     case 'r':output.Append('\r');break;
     case 'u':case 'U':
      int digits=escaped=='u'?4:8;uint scalar;
      if(position+digits>raw.Length||!UInt32.TryParse(raw.Substring(position,digits),NumberStyles.AllowHexSpecifier,CultureInfo.InvariantCulture,out scalar)||scalar>0x10ffff||(scalar>=0xd800&&scalar<=0xdfff))throw Refuse("Unicode 转义无效");
      output.Append(Char.ConvertFromUtf32((int)scalar));position+=digits;break;
     default:throw Refuse("字符串含不支持的转义");
    }
   }
   throw Refuse("字符串未闭合");
  }
  static object Value(string raw) {
   raw=raw.Trim();if(raw.Length==0)throw Refuse("配置值为空");
   if(raw[0]=='"'||raw[0]=='\'') {
    int index=0;string value=ReadString(raw,ref index);
    if(raw.Substring(index).Trim().Length!=0)throw Refuse("字符串后包含未识别内容");
    return value;
   }
   if(raw=="true")return true;if(raw=="false")return false;
   if(raw[0]=='[') {
    var array=new List<string>();int index=1;bool expectsValue=true;
    while(true) {
     while(index<raw.Length&&Char.IsWhiteSpace(raw[index]))index++;
     if(index>=raw.Length)throw Refuse("不支持跨行数组");
     if(raw[index]==']'){index++;if(raw.Substring(index).Trim().Length!=0)throw Refuse("数组后包含未识别内容");return array;}
     if(!expectsValue||(raw[index]!='"'&&raw[index]!='\''))throw Refuse("数组只支持单行字符串元素");
     array.Add(ReadString(raw,ref index));
     while(index<raw.Length&&Char.IsWhiteSpace(raw[index]))index++;
     if(index<raw.Length&&raw[index]==','){index++;expectsValue=true;}
     else if(index<raw.Length&&raw[index]==']')expectsValue=false;
     else throw Refuse("数组分隔符无效");
    }
   }
   long number;
   if(Regex.IsMatch(raw,@"^[+-]?(?:0|[1-9](?:_?[0-9])*)$")&&Int64.TryParse(raw.Replace("_",""),NumberStyles.AllowLeadingSign,CultureInfo.InvariantCulture,out number))return number;
   throw Refuse("只支持单行字符串、整数、布尔值及字符串数组");
  }
  static void Add(Dictionary<string,object> values,string key,object value) {
   if(values.Keys.Any(existing=>existing==key||existing.StartsWith(key+".",StringComparison.Ordinal)||key.StartsWith(existing+".",StringComparison.Ordinal)))throw Refuse("存在重复或冲突的 TOML 键");
   values.Add(key,value);
  }
  static Document Parse(string input) {
   if(input==null||input.Length==0||input.Length>1048576)throw Refuse("配置为空或超过 1 MiB");
   var doc=new Document();Dictionary<string,object> scope=doc.Common;string prefix="";
   var tables=new HashSet<string>(StringComparer.Ordinal);int proxyIndex=-1;
   foreach(string original in input.TrimStart('\ufeff').Replace("\r\n","\n").Replace('\r','\n').Split('\n')) {
    string line=WithoutComment(original).Trim();if(line.Length==0)continue;
    if(line.StartsWith("[[",StringComparison.Ordinal)) {
     if(line!="[[proxies]]")throw Refuse("只支持 provider 的 proxies 数组表");
     scope=new Dictionary<string,object>(StringComparer.Ordinal);doc.Proxies.Add(scope);proxyIndex++;prefix="";continue;
    }
    if(line[0]=='[') {
     if(line[line.Length-1]!=']')throw Refuse("TOML 表头无效");
     string table=Key(line.Substring(1,line.Length-2).Trim());
     if(table=="proxies"||table=="visitors"||table.StartsWith("visitors.",StringComparison.Ordinal))throw Refuse("不是受支持的 provider 配置");
     if(table.StartsWith("proxies.",StringComparison.Ordinal)) {
      if(proxyIndex<0)throw Refuse("代理子表缺少对应的 proxies");
      scope=doc.Proxies[proxyIndex];prefix=table.Substring("proxies.".Length)+".";
     } else {scope=doc.Common;prefix=table+".";}
     string tableId=(Object.ReferenceEquals(scope,doc.Common)?"common:":"proxy:"+proxyIndex.ToString(CultureInfo.InvariantCulture)+":")+table;
     if(!tables.Add(tableId))throw Refuse("同一 TOML 表被重复声明");
     continue;
    }
    int equals=line.IndexOf('=');if(equals<1)throw Refuse("无法识别 TOML 赋值");
    Add(scope,prefix+Key(line.Substring(0,equals).Trim()),Value(line.Substring(equals+1)));
   }
   return doc;
  }
  static string StringValue(Dictionary<string,object> values,string key,bool required,string fallback="") {
   object raw;if(!values.TryGetValue(key,out raw)){if(required)throw Refuse("缺少必要的字面量配置");return fallback;}
   string value=raw as string;if(value==null||(required&&value.Length==0))throw Refuse("必要字段不是非空字面量字符串");
   return value;
  }
  static int Port(Dictionary<string,object> values,string key,int fallback) {
   object value;if(!values.TryGetValue(key,out value))return fallback;
   if(!(value is long)||(long)value<1||(long)value>65535)throw Refuse("端口必须为 1–65535 的整数");
   return (int)(long)value;
  }
  static string Quote(string text) {
   var output=new StringBuilder("\"");
   foreach(char c in text) {
    switch(c) {
     case '"':output.Append("\\\"");break;case '\\':output.Append("\\\\");break;
     case '\b':output.Append("\\b");break;case '\t':output.Append("\\t");break;
     case '\n':output.Append("\\n");break;case '\f':output.Append("\\f");break;case '\r':output.Append("\\r");break;
     default:if(c<0x20||c==0x7f)output.Append("\\u"+((int)c).ToString("X4",CultureInfo.InvariantCulture));else output.Append(c);break;
    }
   }
   return output.Append('"').ToString();
  }
  static string Literal(object value) {
   if(value is string)return Quote((string)value);
   if(value is bool)return (bool)value?"true":"false";
   if(value is long)return ((long)value).ToString(CultureInfo.InvariantCulture);
   var array=value as List<string>;if(array!=null)return "["+String.Join(", ",array.Select(Quote).ToArray())+"]";
   throw Refuse("包含未支持的值类型");
  }
  static void CheckCommon(Dictionary<string,object> common) {
   var transport=new HashSet<string>(new[]{"transport.protocol","transport.tls.enable","transport.tls.disableCustomTLSFirstByte","transport.tls.serverName","transport.tcpMux","transport.heartbeatInterval","transport.heartbeatTimeout","transport.tcpMuxKeepaliveInterval","transport.dialServerTimeout","transport.dialServerKeepalive"},StringComparer.Ordinal);
   foreach(var pair in common) {
    string key=pair.Key;
    if(key=="serverAddr"||key=="serverPort"||key=="user"||key=="loginFailExit"||key=="auth.method"||key=="auth.token"||key=="auth.additionalScopes"||key.StartsWith("log.",StringComparison.Ordinal)||key.StartsWith("webServer.",StringComparison.Ordinal))continue;
    if(transport.Contains(key)) {
     if(key=="transport.protocol") {if(!(pair.Value is string)||!new[]{"tcp","kcp","quic","websocket","wss"}.Contains((string)pair.Value))throw Refuse("不支持该传输协议");}
     else if(key=="transport.tls.serverName") {if(!(pair.Value is string))throw Refuse("TLS serverName 类型无效");}
     else if(key=="transport.tls.enable"||key=="transport.tls.disableCustomTLSFirstByte"||key=="transport.tcpMux") {if(!(pair.Value is bool))throw Refuse("传输开关必须为布尔值");}
     else if(!(pair.Value is long))throw Refuse("传输时间参数必须为整数");
     continue;
    }
    throw Refuse("含需手动迁移的认证、证书、代理、include 或其他高级配置");
   }
   object scopes;if(common.TryGetValue("auth.additionalScopes",out scopes)) {
    var array=scopes as List<string>;if(array==null||array.Any(value=>value!="HeartBeats"&&value!="NewWorkConns")||array.Distinct().Count()!=array.Count)throw Refuse("认证 scopes 不受支持");
   }
  }
  public static string Build(string providerToml,string selectedServer,int bindPort=11436) {
   if(String.IsNullOrEmpty(selectedServer)||selectedServer.Length>253||Uri.CheckHostName(selectedServer)==UriHostNameType.Unknown||selectedServer.Any(c=>c>127||(!Char.IsLetterOrDigit(c)&&c!='.'&&c!='-'&&c!=':')))throw Refuse("所选线路不是有效的 IP 或域名");
   if(bindPort<1||bindPort>65535)throw Refuse("visitor 本地端口不在有效范围内");
   var doc=Parse(providerToml);CheckCommon(doc.Common);
   StringValue(doc.Common,"serverAddr",true);
   int serverPort=Port(doc.Common,"serverPort",7000);
   string method=StringValue(doc.Common,"auth.method",false,"token");if(method!="token")throw Refuse("仅支持显式 token 认证");
   string token=StringValue(doc.Common,"auth.token",true);
   var candidates=doc.Proxies.Where(proxy=>StringValue(proxy,"type",false)=="stcp"&&(StringValue(proxy,"name",false)=="ollama"||Port(proxy,"localPort",0)==11434)).ToList();
   if(candidates.Count!=1)throw Refuse("无法唯一确定 Ollama 的 STCP 代理");
   var selected=candidates[0];object enabled;
   if(selected.TryGetValue("enabled",out enabled)&&(!(enabled is bool)||!(bool)enabled))throw Refuse("目标 STCP 代理未启用");
   if(selected.Keys.Any(key=>key=="allowUsers"||key.StartsWith("plugin.",StringComparison.Ordinal)))throw Refuse("代理包含需手动处理的用户限制或插件");
   string name=StringValue(selected,"name",true),secret=StringValue(selected,"secretKey",true);
   string serverUser=StringValue(doc.Common,"user",false);
   var output=new StringBuilder();
   output.AppendLine("# PRIVATE: contains frps/STCP credentials. Do not commit or share publicly.");
   output.AppendLine("# Run this visitor on the receiving device; bind address is that device's loopback.");
   output.AppendLine("serverAddr = "+Quote(selectedServer));output.AppendLine("serverPort = "+serverPort.ToString(CultureInfo.InvariantCulture));
   output.AppendLine("auth.method = \"token\"");output.AppendLine("auth.token = "+Quote(token));
   object scopes;if(doc.Common.TryGetValue("auth.additionalScopes",out scopes))output.AppendLine("auth.additionalScopes = "+Literal(scopes));
   foreach(var item in doc.Common.Where(pair=>pair.Key.StartsWith("transport.",StringComparison.Ordinal)).OrderBy(pair=>pair.Key,StringComparer.Ordinal))output.AppendLine(item.Key+" = "+Literal(item.Value));
   output.AppendLine();output.AppendLine("[log]");output.AppendLine("to = \"console\"");output.AppendLine("level = \"info\"");
   output.AppendLine();output.AppendLine("[[visitors]]");output.AppendLine("name = \"local-model-visitor\"");output.AppendLine("type = \"stcp\"");
   if(serverUser.Length>0)output.AppendLine("serverUser = "+Quote(serverUser));
   output.AppendLine("serverName = "+Quote(name));output.AppendLine("secretKey = "+Quote(secret));output.AppendLine("bindAddr = \"127.0.0.1\"");output.AppendLine("bindPort = "+bindPort.ToString(CultureInfo.InvariantCulture));
   foreach(string key in new[]{"transport.useEncryption","transport.useCompression"}) {
    object raw;if(selected.TryGetValue(key,out raw)){if(!(raw is bool))throw Refuse("代理传输开关类型无效");output.AppendLine(key+" = "+Literal(raw));}
   }
   return output.ToString();
  }
 }
}
