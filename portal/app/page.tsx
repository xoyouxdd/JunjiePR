import type { Metadata } from "next";

const backendBaseUrl =
  process.env.BACKEND_BASE_URL ?? "https://124.220.229.9/recognition";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "认可签卡绩效登记系统",
  description: "团队认可签卡登记、照片留证、组长复核和月结导出的统一入口。",
};

const checks = [
  "手机和电脑均可访问",
  "组员自助登记并上传照片",
  "组长复核后才计入绩效",
  "管理员可月结导出 Excel",
];

const steps = [
  "组员首次登录：输入员工号后 7 位，自行设置 4 位 PIN。",
  "日常登记：拍照上传签卡，填写认可类型、认可人和内容。",
  "组长复核：确认、退回、作废或标记抽查原卡。",
  "月底处理：管理员导出 Excel，并与正式绩效表对账。",
];

async function getBackendStatus() {
  try {
    const response = await fetch(`${backendBaseUrl}/login`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3500),
    });
    return response.ok ? "运行正常" : "服务异常";
  } catch {
    return "暂时离线";
  }
}

export default async function Home() {
  const backendStatus = await getBackendStatus();
  const isOnline = backendStatus === "运行正常";

  return (
    <main className="site-shell">
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">试运行统一入口</span>
          <h1>认可签卡绩效登记系统</h1>
          <p>
            通过 Codex Sites 的 HTTPS 入口进入正式系统。登记、照片上传、复核与月结数据仍集中保存在公网服务器，原有员工号和 PIN 登录方式保持不变。
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="/system/login">
              进入登录系统
            </a>
            <a className="secondary-action" href="#guide">
              查看试运行说明
            </a>
          </div>
          <p className="security-note">
            为保护测试和员工账号，本入口不再公开展示任何登录凭据。
          </p>
        </div>
        <div className="status-panel" aria-label="系统状态">
          <div>
            <span>公网服务</span>
            <strong className={isOnline ? "status-ok" : "status-warn"}>
              {backendStatus}
            </strong>
          </div>
          <div>
            <span>当前模式</span>
            <strong>影子试运行</strong>
          </div>
          <div>
            <span>数据位置</span>
            <strong>公网服务器集中存储</strong>
          </div>
        </div>
      </section>

      <section className="content-grid">
        <article className="panel">
          <h2>能做什么</h2>
          <ul className="check-list">
            {checks.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </article>

        <article className="panel" id="guide">
          <h2>快速使用</h2>
          <ol className="step-list">
            {steps.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </article>

        <article className="panel account-panel">
          <h2>进入前请确认</h2>
          <dl>
            <div>
              <dt>登录方式</dt>
              <dd>员工号 / PIN</dd>
            </div>
            <div>
              <dt>业务数据</dt>
              <dd>与公网系统实时同步</dd>
            </div>
          </dl>
          <p className="note">
            登录后将进入原有认可签卡系统，组长可继续处理复核、绑定申请和组员记录。
          </p>
        </article>
      </section>

      <footer className="footer">
        Codex Sites 提供安全入口与页面托管；核心业务、SQLite 数据库、照片和 Excel 导出仍由独立服务器负责。
      </footer>
    </main>
  );
}
