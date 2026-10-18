import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowRight, ArrowUp, BookOpen, Check, ChevronRight, CircleHelp,
  Clock3, Download, FileVideo2, FolderOpen, Gauge, Grid2X2,
  History, Home, Image as ImageIcon, Info, Layers3, Library,
  MessageSquareText, MoreHorizontal, PanelLeftClose, Play, Plus,
  Search, Settings, Sparkles, UploadCloud, Video, WandSparkles, X, Zap
} from 'lucide-react';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, '') ?? '';
const demoVideoPath = '/demo/trail-runner-demo.webm';

const scenesSeed = [
  { time: 0, label: 'Valley reveal', note: 'A wide sunrise view establishes the alpine setting.', image: '/demo/scene-opening.jpg' },
  { time: 3, label: 'Waterfall', note: 'Fog parts to reveal a waterfall beyond the trail.', image: '/demo/scene-waterfall.jpg' },
  { time: 7, label: 'Runner advances', note: 'The runner moves toward the ridge in orange gear.', image: '/demo/scene-runner.jpg' },
  { time: 10, label: 'Summit light', note: 'Warm light breaks across the final mountain range.', image: '/demo/trail-runner.png' }
];

const promptSuggestions = [
  'Summarize this video in 3 points',
  'What changes between the first and last scene?',
  'Describe the setting and mood'
];

function formatTime(value = 0) {
  const seconds = Math.max(0, Math.floor(value));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function makeReply(question, duration) {
  const q = question.toLowerCase();
  if (q.includes('summar')) return 'The clip opens with a sweeping alpine landscape at [[00:00]], reveals the waterfall and valley depth near [[00:03]], then follows a trail runner toward the sunlit ridge at [[00:07]]. The visual story feels calm, focused, and adventurous.';
  if (q.includes('change') || q.includes('first and last')) return 'The opening at [[00:00]] prioritizes the landscape, scale, and weather. By [[00:10]], the runner has become the narrative anchor and the warmer light makes the ending feel more purposeful and optimistic.';
  if (q.includes('setting') || q.includes('mood')) return 'The setting is a misty alpine valley with evergreen slopes, exposed rock, a distant waterfall, and snow-lined peaks. At [[00:03]] the cool fog contrasts with the warm sunrise, creating a serene but adventurous mood.';
  return `The strongest visual evidence appears around [[${formatTime(Math.min(7, duration * .58))}]]. The orange-clad runner is framed against the valley, with fog and sunrise defining the depth and atmosphere.`;
}

function TimestampText({ text, onSeek }) {
  return <>{text.split(/(\[\[\d{2}:\d{2}\]\])/g).map((part, index) => {
    const match = part.match(/\[\[(\d{2}):(\d{2})\]\]/);
    if (!match) return <React.Fragment key={index}>{part}</React.Fragment>;
    const seconds = Number(match[1]) * 60 + Number(match[2]);
    return <button key={index} className="timestamp" onClick={() => onSeek(seconds)}><Play size={10} fill="currentColor" />{match[1]}:{match[2]}</button>;
  })}</>;
}

function Logo() {
  return <div className="brand"><span className="brand-mark"><i /><i /><i /></span><span>understand<span>video</span></span><b>AI</b></div>;
}

function App() {
  const [page, setPage] = useState('home');
  const [panel, setPanel] = useState('ask');
  const [videoUrl, setVideoUrl] = useState('');
  const [videoId, setVideoId] = useState('');
  const [fileName, setFileName] = useState('Alpine trail — demo.webm');
  const [duration, setDuration] = useState(12);
  const [currentTime, setCurrentTime] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [remaining, setRemaining] = useState(50);
  const [toast, setToast] = useState('');
  const videoRef = useRef(null);
  const fileRef = useRef(null);
  const chatEndRef = useRef(null);

  const scenes = useMemo(() => scenesSeed.map((scene, index) => ({
    ...scene,
    time: Math.min(scene.time, Math.max(0, duration - (4 - index)))
  })), [duration]);

  useEffect(() => () => { if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl); }, [videoUrl]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);
  useEffect(() => { if (!toast) return; const timeout = setTimeout(() => setToast(''), 2600); return () => clearTimeout(timeout); }, [toast]);

  async function sendUpload(file) {
    const form = new FormData();
    form.append('file', file);
    try {
      const response = await fetch(`${API_BASE}/api/videos/upload`, { method: 'POST', body: form });
      if (!response.ok) throw new Error('Upload failed');
      const uploaded = await response.json();
      setVideoId(uploaded.id);
      setToast('Video safely added to your library');
    } catch {
      setVideoId('');
      setToast('Demo mode · analysis stays in this browser');
    }
  }

  async function loadFile(file, { demo = false } = {}) {
    if (!file || !file.type.startsWith('video/')) {
      setToast('Choose an MP4, MOV, WebM, or AVI video');
      return;
    }
    if (file.size > 100 * 1024 * 1024) {
      setToast('That video is larger than the 100 MB limit');
      return;
    }
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
    setVideoUrl(demo ? demoVideoPath : URL.createObjectURL(file));
    setFileName(demo ? 'Alpine trail — demo.webm' : file.name);
    setVideoId('');
    setMessages([]);
    setCurrentTime(0);
    setProcessing(true);
    setProgress(8);
    setPage('studio');
    const timer = setInterval(() => setProgress(old => Math.min(94, old + Math.ceil(Math.random() * 11))), 160);
    await sendUpload(file);
    setTimeout(() => {
      clearInterval(timer);
      setProgress(100);
      setProcessing(false);
    }, 1100);
  }

  async function loadDemo() {
    setToast('Loading the demo film…');
    const response = await fetch(demoVideoPath);
    const blob = await response.blob();
    await loadFile(new File([blob], 'alpine-trail-demo.webm', { type: 'video/webm' }), { demo: true });
  }

  function seekTo(seconds) {
    if (!videoRef.current) return;
    videoRef.current.currentTime = Math.min(seconds, videoRef.current.duration || seconds);
    videoRef.current.play().catch(() => {});
  }

  function completeClientReply(question) {
    const reply = makeReply(question, duration);
    let cursor = 0;
    const interval = setInterval(() => {
      cursor += 3;
      const shown = reply.slice(0, cursor);
      setMessages(old => [...old.filter(item => item.id !== 'stream'), { id: 'stream', role: 'assistant', text: shown }]);
      if (cursor >= reply.length) {
        clearInterval(interval);
        setStreaming(false);
        setMessages(old => old.map(item => item.id === 'stream' ? { ...item, id: crypto.randomUUID() } : item));
      }
    }, 14);
  }

  async function ask(value = input) {
    const question = value.trim();
    if (!question || streaming || processing) return;
    setInput('');
    setMessages(old => [...old, { role: 'user', text: question }]);
    setStreaming(true);
    if (videoId) {
      try {
        const response = await fetch(`${API_BASE}/api/chat/stream`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ video_id: videoId, message: question })
        });
        if (!response.ok || !response.body) throw new Error('Stream unavailable');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', shown = '';
        while (true) {
          const { value: chunk, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(chunk, { stream: true });
          const events = buffer.split('\n\n');
          buffer = events.pop() || '';
          for (const event of events) {
            const line = event.split('\n').find(part => part.startsWith('data: '));
            if (!line) continue;
            const data = JSON.parse(line.slice(6));
            if (data.type === 'meta') setRemaining(data.remaining);
            if (data.type === 'token') {
              shown += data.text;
              setMessages(old => [...old.filter(item => item.id !== 'stream'), { id: 'stream', role: 'assistant', text: shown }]);
            }
          }
        }
        setMessages(old => old.map(item => item.id === 'stream' ? { ...item, id: crypto.randomUUID() } : item));
        setStreaming(false);
        return;
      } catch {
        setToast('Connection changed · continued in demo mode');
      }
    }
    completeClientReply(question);
  }

  const uploadDropHandlers = {
    onDragOver: e => { e.preventDefault(); setDragging(true); },
    onDragLeave: () => setDragging(false),
    onDrop: e => { e.preventDefault(); setDragging(false); loadFile(e.dataTransfer.files[0]); }
  };

  return <div className="app-shell">
    <aside className="side-rail">
      <Logo />
      <nav className="main-nav">
        <button className={page === 'home' ? 'active' : ''} onClick={() => setPage('home')}><Home size={17} /><span>Home</span></button>
        <button className={page === 'studio' ? 'active' : ''} onClick={() => videoUrl ? setPage('studio') : fileRef.current?.click()}><WandSparkles size={17} /><span>Studio</span><kbd>⌘K</kbd></button>
        <button className={page === 'library' ? 'active' : ''} onClick={() => setPage('library')}><Library size={17} /><span>Library</span></button>
      </nav>
      <div className="nav-label">Workspace</div>
      <nav className="main-nav secondary">
        <button onClick={() => setToast('Team workspaces are on the roadmap')}><Layers3 size={17} /><span>Shared with me</span></button>
        <button onClick={() => setToast('No exports yet')}><Download size={17} /><span>Exports</span></button>
      </nav>
      <div className="rail-spacer" />
      <div className="usage-card">
        <div><span>Monthly usage</span><b>62%</b></div>
        <i><em /></i>
        <small>{remaining} AI prompts remaining</small>
      </div>
      <nav className="main-nav secondary bottom-nav">
        <button onClick={() => setToast('Help center coming soon')}><CircleHelp size={17} /><span>Help center</span></button>
        <button onClick={() => setToast('Settings are ready for the next milestone')}><Settings size={17} /><span>Settings</span></button>
      </nav>
      <button className="profile-button"><span>AK</span><div><strong>Aarav Khanal</strong><small>Free workspace</small></div><MoreHorizontal size={16} /></button>
    </aside>

    <div className="app-main">
      <header className="topbar">
        <div className="mobile-brand"><Logo /></div>
        <button className="search-command"><Search size={15} /><span>Search your videos</span><kbd>⌘ /</kbd></button>
        <div className="top-actions"><span className="system-status"><i />Systems ready</span><button className="icon-button"><Clock3 size={17} /></button><button className="new-video" onClick={() => fileRef.current?.click()}><Plus size={15} />New video</button></div>
      </header>

      {page === 'home' && <main className="home-page">
        <section className="hero-grid">
          <div className="hero-copy">
            <span className="hero-pill"><Sparkles size={13} />Built to see the whole story</span>
            <h1>Watch less.<br /><em>Understand more.</em></h1>
            <p>Turn any video into a searchable conversation. Find moments, extract meaning, and get clear answers with exact timestamps.</p>
            <div className="hero-actions"><button className="primary-cta" onClick={() => fileRef.current?.click()}><UploadCloud size={17} />Upload a video</button><button className="ghost-cta" onClick={loadDemo}><Play size={15} fill="currentColor" />Try the 12 sec demo</button></div>
            <div className="trust-row"><span><Check size={12} />Private by default</span><span><Check size={12} />100 MB uploads</span><span><Check size={12} />Timestamped answers</span></div>
          </div>
          <button className="hero-film" onClick={loadDemo} aria-label="Play demo video">
            <img src="/demo/trail-runner.png" alt="Trail runner overlooking a misty mountain valley" />
            <div className="film-top"><span>DEMO FILM</span><b>00:12</b></div>
            <div className="play-orb"><Play size={20} fill="currentColor" /></div>
            <div className="film-caption"><div><small>AI-GENERATED SAMPLE</small><strong>Across the alpine valley</strong></div><span>4 scenes</span></div>
          </button>
        </section>

        <section className={`quick-upload ${dragging ? 'dragging' : ''}`} {...uploadDropHandlers}>
          <div className="upload-badge"><UploadCloud size={20} /></div>
          <div><strong>Drop a video to start understanding</strong><span>MP4, MOV, WebM or AVI · up to 100 MB</span></div>
          <button onClick={() => fileRef.current?.click()}>Browse files <ArrowRight size={14} /></button>
        </section>

        <section className="dashboard-row">
          <div className="recent-section">
            <div className="section-title"><div><span>YOUR SPACE</span><h2>Recent videos</h2></div><button onClick={() => setPage('library')}>View library <ArrowRight size={13} /></button></div>
            <div className="media-grid">
              <button className="media-card featured" onClick={loadDemo}><div className="thumb"><img src="/demo/scene-runner.jpg" alt="Runner scene thumbnail" /><span className="duration">00:12</span><span className="thumb-play"><Play size={13} fill="currentColor" /></span></div><div className="media-info"><strong>Across the alpine valley</strong><span><i />Analyzed · 4 scenes</span></div></button>
              <button className="media-card add-card" onClick={() => fileRef.current?.click()}><div><Plus size={21} /></div><strong>Add your first video</strong><span>Your next analysis starts here</span></button>
            </div>
          </div>
          <aside className="insight-card"><div className="insight-icon"><Gauge size={19} /></div><span>WORKSPACE PULSE</span><h3>Every frame becomes searchable context.</h3><p>Scene mapping, timeline-aware answers, and conversation memory work together in one focused space.</p><div className="metric-row"><div><b>4</b><small>scene types</small></div><div><b>&lt;1s</b><small>timestamp jump</small></div><div><b>∞</b><small>follow-ups</small></div></div></aside>
        </section>
      </main>}

      {page === 'library' && <main className="library-page">
        <div className="page-heading"><div><span className="overline">VIDEO LIBRARY</span><h1>Your visual knowledge base</h1><p>Search, revisit, and continue every video conversation.</p></div><button className="primary-cta" onClick={() => fileRef.current?.click()}><Plus size={16} />Add video</button></div>
        <div className="library-toolbar"><div className="library-search"><Search size={15} /><input placeholder="Search title, topic, or transcript" /></div><button><Grid2X2 size={15} />All videos</button></div>
        <div className="library-grid"><button className="library-video" onClick={loadDemo}><div><img src="/demo/trail-runner.png" alt="Mountain valley demo" /><span><Play size={15} fill="currentColor" /></span></div><strong>Across the alpine valley</strong><small>Demo · 4 scenes · 12 seconds</small></button><button className="library-upload" onClick={() => fileRef.current?.click()}><UploadCloud size={22} /><strong>Upload another video</strong><small>Build your searchable library</small></button></div>
      </main>}

      {page === 'studio' && <main className="studio-page">
        <section className="studio-video">
          <div className="studio-heading"><div><span className="overline">VIDEO STUDIO</span><h1>{fileName}</h1></div><div className="studio-meta"><span><i />{processing ? 'Analyzing' : 'Ready'}</span><button onClick={() => fileRef.current?.click()}><Plus size={14} />Replace</button></div></div>
          <div className="video-frame">
            {videoUrl ? <video ref={videoRef} src={videoUrl} poster="/demo/trail-runner.png" controls onTimeUpdate={e => setCurrentTime(e.currentTarget.currentTime)} onLoadedMetadata={e => setDuration(e.currentTarget.duration || 12)} /> : <button className="empty-player" onClick={() => fileRef.current?.click()}><UploadCloud size={26} /><strong>Choose a video</strong></button>}
            {processing && <div className="analysis-overlay"><div className="scan-line" /><span className="analysis-orb"><Sparkles size={22} /></span><strong>Reading the visual story</strong><p>Mapping scenes · tracking motion · creating context</p><div className="analysis-progress"><i style={{ width: `${progress}%` }} /></div><small>{progress}% complete</small></div>}
          </div>
          {!processing && videoUrl && <div className="scene-map">
            <div className="scene-map-head"><div><span className="overline">SCENE MAP</span><strong>{scenes.length} chapters found</strong></div><span>{formatTime(currentTime)} / {formatTime(duration)}</span></div>
            <div className="timeline"><i style={{ width: `${duration ? currentTime / duration * 100 : 0}%` }} />{scenes.map((scene, index) => <button key={scene.label} onClick={() => seekTo(scene.time)} style={{ left: `${duration ? scene.time / duration * 100 : 0}%` }}><span>{index + 1}</span></button>)}</div>
            <div className="scene-cards">{scenes.map((scene, index) => <button key={scene.label} onClick={() => seekTo(scene.time)} className={currentTime >= scene.time && currentTime < (scenes[index + 1]?.time ?? Infinity) ? 'active' : ''}><img src={scene.image} alt="" /><div><span>{formatTime(scene.time)}</span><strong>{scene.label}</strong></div></button>)}</div>
          </div>}
        </section>

        <aside className="ai-panel">
          <div className="panel-tabs"><button className={panel === 'ask' ? 'active' : ''} onClick={() => setPanel('ask')}><MessageSquareText size={15} />Ask AI</button><button className={panel === 'overview' ? 'active' : ''} onClick={() => setPanel('overview')}><Info size={15} />Overview</button><button className="panel-close" onClick={() => setPage('home')}><PanelLeftClose size={16} /></button></div>
          <div className="panel-content">
            {panel === 'overview' ? <div className="overview-panel"><span className="ai-chip"><Sparkles size={12} />AI OVERVIEW</span><h2>A quiet pursuit across an immense alpine landscape.</h2><p>The scene follows a lone trail runner through a mist-covered mountain valley at sunrise. The runner’s orange jacket creates a strong focal point against deep greens and cool fog.</p><div className="overview-block"><span>KEY DETAILS</span><ul><li><ImageIcon size={14} />Mountain valley and waterfall</li><li><Video size={14} />One runner moving forward</li><li><Clock3 size={14} />Warm dawn light across 4 scenes</li></ul></div><div className="topic-tags"><span>outdoors</span><span>trail running</span><span>sunrise</span><span>landscape</span></div></div> :
            processing ? <div className="panel-empty"><span className="ai-loader"><Sparkles size={22} /></span><h2>Building visual memory</h2><p>The conversation unlocks as soon as the scene map is ready.</p></div> : messages.length === 0 ? <div className="panel-ready"><span className="ai-chip"><Sparkles size={12} />VIDEO READY</span><h2>What do you want to know?</h2><p>I can reason across scenes and take you straight to the evidence.</p><div className="suggestion-list">{promptSuggestions.map(prompt => <button key={prompt} onClick={() => ask(prompt)}><span>{prompt}</span><ArrowUp size={13} /></button>)}</div><div className="mini-overview"><div><img src="/demo/scene-waterfall.jpg" alt="Waterfall scene" /><span><strong>4 scenes mapped</strong><small>Landscape · movement · light</small></span></div><button onClick={() => setPanel('overview')}>Open overview <ChevronRight size={12} /></button></div></div> :
            <div className="messages">{messages.map((message, index) => <div key={message.id || index} className={`message ${message.role}`}>{message.role === 'assistant' && <span className="assistant-avatar"><Sparkles size={13} /></span>}<div><span className="message-author">{message.role === 'assistant' ? 'UNDERSTAND AI' : 'YOU'}</span><p>{message.role === 'assistant' ? <TimestampText text={message.text} onSeek={seekTo} /> : message.text}</p></div></div>)}{streaming && <span className="thinking"><i />Reading the scene…</span>}<div ref={chatEndRef} /></div>}
          </div>
          <div className="composer-area"><div className={`composer ${processing || !videoUrl ? 'disabled' : ''}`}><textarea rows={1} value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder="Ask anything about this video…" disabled={processing || !videoUrl} /><button onClick={() => ask()} disabled={!input.trim() || streaming}><ArrowUp size={16} /></button></div><span><Sparkles size={10} />Answers link back to the exact moment</span></div>
        </aside>
      </main>}
    </div>

    <input ref={fileRef} type="file" hidden accept="video/mp4,video/quicktime,video/webm,video/x-msvideo" onChange={e => loadFile(e.target.files[0])} />
    {toast && <div className="toast"><Check size={14} />{toast}</div>}
  </div>;
}

const rootElement = document.getElementById('root');
window.__understandVideoRoot ||= createRoot(rootElement);
window.__understandVideoRoot.render(<App />);
