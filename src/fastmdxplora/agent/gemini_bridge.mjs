/* Official Gemini client owns authentication; this bridge exposes no credentials. */
import {readFile} from "node:fs/promises";
import {pathToFileURL} from "node:url";
import {dirname, join, resolve} from "node:path";
import {randomUUID} from "node:crypto";

export function reasoningLevels(model) {
  if(/^gemini-3\.[78]-flash(-preview)?$/.test(model) || /^gemini-3\.1-pro(-preview)?$/.test(model)) return ['low','medium','high'];
  if(/^gemini-3-pro(-preview)?$/.test(model)) return ['low','high'];
  if(/^gemini-(3\.[56]-flash|3\.[15]-flash-lite|3-flash)(-preview)?$/.test(model)) return ['minimal','low','medium','high'];
  if(/^gemini-2\.5-pro(-preview(-\d{2}-\d{2})?)?$/.test(model)) return ['128','1024','4096','8192','16384','32768'];
  if(/^gemini-2\.5-flash(-lite)?(-preview(-\d{2}-\d{2})?)?$/.test(model)) return ['0','512','1024','4096','8192','16384','24576'];
  return [];
}
export function requestFor(model, prompt, reasoning) {
  if(typeof model!=="string" || !/^gemini-[a-z0-9.-]{1,100}$/.test(model) ||
     typeof prompt!=="string" || Buffer.byteLength(prompt)>1000000) throw new Error("Invalid explanation input.");
  const request = {model, contents:[{role:"user",parts:[{text:prompt}]}],
    config:{tools:[],toolConfig:{functionCallingConfig:{mode:"NONE"}},candidateCount:1}};
  if(reasoning != null && reasoning !== 'default') {
    if(!reasoningLevels(model).includes(reasoning)) throw new Error('Unsupported model reasoning level.');
    request.config.thinkingConfig = model.startsWith('gemini-2.5-')
      ? {thinkingBudget:Number(reasoning)} : {thinkingLevel:reasoning.toUpperCase()};
  }
  return request;
}
export function validateReply(reply) {
  const candidates=reply?.candidates;
  if(!Array.isArray(candidates) || candidates.length!==1 || candidates[0].finishReason!=="STOP")
    throw new Error("Gemini did not complete the explanation.");
  const parts=candidates[0].content?.parts;
  if(!Array.isArray(parts) || parts.some(part=>part.functionCall || part.functionResponse || part.executableCode || part.codeExecutionResult))
    throw new Error("Gemini returned a tool action; this Agent cannot execute it.");
  const text=parts.filter(part=>!part.thought && typeof part.text==="string").map(part=>part.text).join("").trim();
  if(!text || Buffer.byteLength(text)>2000000) throw new Error("Gemini returned no bounded explanation.");
  return text;
}
export async function nativeCore(entry) {
  const folder=dirname(entry);
  const metadata=JSON.parse(await readFile(join(folder,"..","package.json"),"utf8"));
  if(metadata.name!=="@google/gemini-cli" || metadata.version!=="0.62.0")
    throw new Error("This Gemini client version has not been verified for the subscription bridge.");
  // Resolve the public core export used by this exact installed client bundle.
  const index=await readFile(entry,"utf8");
  const match=index.match(/import\("\.\/(dist-[A-Z0-9]+\.js)"\)/);
  if(!match) throw new Error("The official Gemini core export is unavailable.");
  const core=await import(pathToFileURL(join(folder,match[1])).href);
  if(typeof core.getOauthClient!=="function" || typeof core.CodeAssistServer!=="function")
    throw new Error("The official Gemini client interface is unavailable.");
  return core;
}
async function run() {
  const entry=resolve(process.argv[2]), action=process.argv[3];
  if(!["login","identity","models","complete","logout","probe"].includes(action)) throw new Error("Unknown connection action.");
  const core=await nativeCore(entry);
  const profile=resolve(process.env.GEMINI_CLI_HOME);
  if(!resolve(core.Storage.getGlobalGeminiDir()).startsWith(profile+"\\") &&
     !resolve(core.Storage.getGlobalGeminiDir()).startsWith(profile+"/"))
    throw new Error("The Gemini client profile is not isolated.");
  if(process.env.GEMINI_FORCE_FILE_STORAGE!=="true" || process.env.GEMINI_FORCE_ENCRYPTED_FILE_STORAGE!=="true" ||
     !(await new core.KeychainService("gemini-cli-oauth").isUsingFileFallback()))
    throw new Error("The Gemini client credential storage is not isolated.");
  if(action==="probe") return {version:"0.62.0",profile:core.Storage.getGlobalGeminiDir(),storage:"isolated official encrypted file",tools:0,backend:"official content generator; no agent tool registry"};
  if(action==="logout") {
    await core.clearCachedCredentialFile();
    return {logged_out:!(new core.UserAccountManager()).getCachedGoogleAccount()};
  }
  // These explicit ACP-style controls only allow browser launch after a user's
  // login gesture. Identity/model/inference never start another login flow.
  const config={getProxy:()=>undefined,isBrowserLaunchSuppressed:()=>action!=="login",
    isInteractive:()=>false,getAcpMode:()=>true};
  const oauth=await core.getOauthClient(core.AuthType.LOGIN_WITH_GOOGLE,config);
  const metadata=await oauth.request({url:"https://www.googleapis.com/oauth2/v2/userinfo",method:"GET",timeout:20000});
  const user=metadata.data;
  if(!user || typeof user.id!=="string" || !/^\d{1,100}$/.test(user.id) ||
     user.verified_email!==true || typeof user.email!=="string" || user.email.length>320 || /[\x00-\x1f]/.test(user.email))
    throw new Error("Google did not confirm the account identity.");
  const server=new core.CodeAssistServer(oauth,undefined,{},randomUUID());
  const account=await server.loadCodeAssist({metadata:{ideType:"IDE_UNSPECIFIED",platform:"PLATFORM_UNSPECIFIED",pluginType:"GEMINI"}});
  // Do not enroll a new account or silently choose a billed Cloud project.
  if(!account?.currentTier || !account.cloudaicompanionProject || account.ineligibleTiers?.length)
    throw new Error("Finish Gemini Code Assist enrollment in the official client before connecting this account.");
  const identity={subject:user.id,email:user.email,tier:String(account.paidTier?.name || account.currentTier.name || account.currentTier.id || "Google account").slice(0,200)};
  if(action==="identity" || action==="login") return identity;
  server.projectId=account.cloudaicompanionProject;
  const quota=await server.retrieveUserQuota({project:server.projectId});
  const models=Array.from(new Set((quota.buckets || []).map(row=>row.modelId).filter(id=>typeof id==="string" && /^gemini-[a-z0-9.-]{1,100}$/.test(id))))
    .slice(0,100).map(id=>({id,label:id}));
  if(!models.length) throw new Error("Gemini did not return an available account model catalog.");
  if(action==="models") return {identity,models};
  let input="";
  for await(const chunk of process.stdin) {input+=chunk; if(Buffer.byteLength(input)>1100000) throw new Error("Explanation input exceeds its limit.");}
  const request=JSON.parse(input);
  if(request.subject!==identity.subject || !models.some(row=>row.id===request.model)) throw new Error("Account or model changed.");
  // generateContent sends no enabledCreditTypes. No automatic credit spending,
  // tools, hooks, MCP, skills, project files or autonomous client session exist.
  const result=await server.generateContent(requestFor(request.model,request.prompt,request.reasoning),randomUUID());
  return {identity,text:validateReply(result),model:request.model};
}
if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) {
  try { const result=await run(); process.stdout.write(JSON.stringify({ok:true,...result})+"\n"); process.exit(0); }
  catch { process.stdout.write(JSON.stringify({ok:false,error:"The official Gemini subscription request could not be verified. Check login, enrollment, account quota and client version."})+"\n"); process.exit(1); }
}
