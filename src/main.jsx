import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createClient } from '@supabase/supabase-js';
import {
  ArrowRight, ArrowUp, Check, ChevronRight,
  Clock3, Download, FileVideo2, Grid2X2,
  Home, Image as ImageIcon, Layers3, Library,
  Link2, MessageSquareText, PanelLeftClose, Play, Plus,
  Search, Sparkles, UploadCloud, Video
} from 'lucide-react';
import './styles.css';
import { formatTime, timestampPattern, timestampToSeconds } from './video-utils.js';

const API_BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, '') ?? '';
const DEMO_VIDEO_PATH = '/demo/trail-runner-demo.webm';
const DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL?.trim();
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();
const supabase = SUPABASE_URL && SUPABASE_KEY ? createClient(SUPABASE_URL, SUPABASE_KEY) : null;
const nativeFetch = window.fetch.bind(window);

function formatBytes(value) {
  if (!Number.isFinite(Number(value)) || Number(value) <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function formatEta(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))}s remaining`;
  return `${Math.ceil(seconds / 60)}m remaining`;
}

function importMetrics(job) {
  if (!job || ['failed', 'cancelled'].includes(job.status)) return '';
  const parts = [];
  const downloaded = formatBytes(job.downloaded_bytes);
  const total = formatBytes(job.total_bytes);
  if (downloaded) parts.push(total ? `${downloaded} of ${total}` : downloaded);
  const speed = formatBytes(job.speed_bps);
  if (speed) parts.push(`${speed}/s`);
  const eta = formatEta(job.eta_seconds);
  if (eta) parts.push(eta);
  if (job.attempts > 1) parts.push(`attempt ${job.attempts}`);
  return parts.join(' · ');
}

async function apiFetch(input, init = {}) {
  const headers = new Headers(init.headers || {});
  if (supabase) {
    const { data } = await supabase.auth.getSession();
    if (data.session?.access_token) headers.set('Authorization', `Bearer ${data.session.access_token}`);
  }
  return nativeFetch(input, { ...init, headers });
}

async function protectedObjectUrl(path) {
  const response = await apiFetch(path);
  if (!response.ok) throw new Error('Private media is unavailable');
  return URL.createObjectURL(await response.blob());
}

function ProtectedImage({ src, alt = '', ...props }) {
  const [objectUrl, setObjectUrl] = useState('');
  useEffect(() => {
    let active = true;
    let createdUrl = '';
    protectedObjectUrl(src).then(url => {
      createdUrl = url;
      if (active) setObjectUrl(url);
      else URL.revokeObjectURL(url);
    }).catch(() => {});
    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [src]);
  return objectUrl ? <img src={objectUrl} alt={alt} {...props} /> : null;
}

const DEMO_SCENES = [
  { time: 0, label: 'Valley reveal', note: 'A wide sunrise view establishes the alpine setting.', image: '/demo/scene-opening.jpg' },
  { time: 3, label: 'Waterfall', note: 'Fog parts to reveal a waterfall beyond the trail.', image: '/demo/scene-waterfall.jpg' },
  { time: 7, label: 'Runner advances', note: 'The runner moves toward the ridge in orange gear.', image: '/demo/scene-runner.jpg' },
  { time: 10, label: 'Summit light', note: 'Warm light breaks across the final mountain range.', image: '/demo/trail-runner.png' }
];

const DEMO_OVERVIEW = {
  summary: 'A trail runner moves through a misty alpine valley as warm sunrise light breaks across the mountains.',
  tags: ['outdoors', 'trail running', 'sunrise', 'landscape'],
  generated: true
};

const promptSuggestions = [
  'Summarize this video in 3 points',
  'What changes between the first and last scene?',
  'Describe the setting and mood'
];

function analysisLabel(video) {
  if (video.analysis_status === 'ready') return video.poster_url ? 'Timeline ready' : 'Ready';
  if (video.analysis_status === 'failed') return 'Timeline needs retry';
  if (video.analysis_status === 'processing') return `Mapping moments · ${video.analysis_progress || 0}%`;
  return 'Waiting to analyze';
}

function makeReply(question, duration, isDemo) {
  if (!isDemo) {
    return 'This video is open locally, but I cannot inspect its frames without the API and a configured vision model. Start the API or connect a model, then ask again—I will not invent details about your footage.';
  }
  const q = question.toLowerCase();
  if (q.includes('summar')) return 'The clip opens with a sweeping alpine landscape at [[00:00]], reveals the waterfall and valley depth near [[00:03]], then follows a trail runner toward the sunlit ridge at [[00:07]]. The visual story feels calm, focused, and adventurous.';
  if (q.includes('change') || q.includes('first and last')) return 'The opening at [[00:00]] prioritizes the landscape, scale, and weather. By [[00:10]], the runner has become the narrative anchor and the warmer light makes the ending feel more purposeful and optimistic.';
  if (q.includes('setting') || q.includes('mood')) return 'The setting is a misty alpine valley with evergreen slopes, exposed rock, a distant waterfall, and snow-lined peaks. At [[00:03]] the cool fog contrasts with the warm sunrise, creating a serene but adventurous mood.';
  return `The strongest visual evidence appears around [[${formatTime(Math.min(7, duration * .58))}]]. The orange-clad runner is framed against the valley, with fog and sunrise defining the depth and atmosphere.`;
}

function TimestampText({ text, onSeek }) {
  return <>{text.split(timestampPattern()).map((part, index) => {
    const match = part.match(/\[\[(\d{2}):(\d{2})\]\]/);
    if (!match) return <React.Fragment key={index}>{part}</React.Fragment>;
    const seconds = timestampToSeconds(match[1], match[2]);
    return <button key={index} className="timestamp" onClick={() => onSeek(seconds)}><Play size={10} fill="currentColor" />{match[1]}:{match[2]}</button>;
  })}</>;
}

function Logo() {
  return <div className="brand"><span className="brand-mark"><i /><i /><i /></span><span>understandvideo</span></div>;
}

function WorkspaceApp({ previewMode = false, onRequestSignIn = () => {} }) {
  const [page, setPage] = useState(previewMode ? 'studio' : 'home');
  const [panel, setPanel] = useState('ask');
  const [videoUrl, setVideoUrl] = useState(previewMode ? DEMO_VIDEO_PATH : '');
  const [videoId, setVideoId] = useState('');
  const [sourceType, setSourceType] = useState(previewMode ? 'demo' : 'none');
  const [sourceUrl, setSourceUrl] = useState('');
  const [conversationId, setConversationId] = useState('');
  const [conversations, setConversations] = useState([]);
  const [showConversations, setShowConversations] = useState(false);
  const [editingConversationId, setEditingConversationId] = useState('');
  const [pendingConversationDeleteId, setPendingConversationDeleteId] = useState('');
  const [conversationDraft, setConversationDraft] = useState('');
  const [fileName, setFileName] = useState('Alpine trail — demo.webm');
  const [duration, setDuration] = useState(12);
  const [currentTime, setCurrentTime] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [analysisStatus, setAnalysisStatus] = useState(previewMode ? 'ready' : 'idle');
  const [analysisError, setAnalysisError] = useState('');
  const [persistedScenes, setPersistedScenes] = useState([]);
  const [transcriptStatus, setTranscriptStatus] = useState('idle');
  const [transcriptError, setTranscriptError] = useState('');
  const [transcriptSegments, setTranscriptSegments] = useState([]);
  const [transcriptChapters, setTranscriptChapters] = useState([]);
  const [transcriptQuery, setTranscriptQuery] = useState('');
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [annotations, setAnnotations] = useState([]);
  const [annotationDraft, setAnnotationDraft] = useState('');
  const [videoTags, setVideoTags] = useState([]);
  const [tagDraft, setTagDraft] = useState('');
  const [lastShareUrl, setLastShareUrl] = useState('');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [playbackError, setPlaybackError] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [maxUploadBytes, setMaxUploadBytes] = useState(DEFAULT_MAX_UPLOAD_BYTES);
  const [apiOnline, setApiOnline] = useState(false);
  const [overview, setOverview] = useState(DEMO_OVERVIEW);
  const [libraryQuery, setLibraryQuery] = useState('');
  const [libraryVideos, setLibraryVideos] = useState([]);
  const [urlInput, setUrlInput] = useState('');
  const [importingUrl, setImportingUrl] = useState(false);
  const [importJob, setImportJob] = useState(null);
  const [importHint, setImportHint] = useState(null);
  const [repairingImport, setRepairingImport] = useState(false);
  const [editingVideoId, setEditingVideoId] = useState('');
  const [editingName, setEditingName] = useState('');
  const [pendingDeleteId, setPendingDeleteId] = useState('');
  const [compareSelection, setCompareSelection] = useState([]);
  const [comparison, setComparison] = useState(null);
  const [toast, setToast] = useState('');
  const videoRef = useRef(null);
  const fileRef = useRef(null);
  const chatEndRef = useRef(null);
  const librarySearchRef = useRef(null);
  const urlInputRef = useRef(null);
  const analysisPollRef = useRef(null);
  const activeVideoIdRef = useRef('');
  const activeConversationIdRef = useRef('');
  const transcriptPollRef = useRef(null);
  const importPollRef = useRef(null);
  const pendingSeekRef = useRef(null);
  const handledShareRef = useRef(false);

  const scenes = useMemo(() => {
    if (sourceType === 'demo') {
      return DEMO_SCENES.map((scene, index) => ({
        ...scene,
        time: Math.min(scene.time, Math.max(0, duration - (4 - index)))
      }));
    }
    if (persistedScenes.length > 0) return persistedScenes;
    const labels = ['Opening', 'Early context', 'Later moment', 'Conclusion'];
    const ratios = [0, .25, .58, .86];
    return labels.map((label, index) => ({ label, time: Math.max(0, duration * ratios[index]) }));
  }, [duration, persistedScenes, sourceType]);

  const formattedUploadLimit = `${Math.round(maxUploadBytes / (1024 * 1024))} MB`;
  const showDemoInLibrary = 'across the alpine valley demo mountains trail running sunrise landscape'.includes(libraryQuery.trim().toLowerCase());
  const filteredLibraryVideos = useMemo(() => libraryVideos.filter(video => video.source_type !== 'demo'), [libraryVideos]);
  const currentConversation = conversations.find(item => item.id === conversationId);

  useEffect(() => () => { if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl); }, [videoUrl]);
  useEffect(() => () => clearTimeout(analysisPollRef.current), []);
  useEffect(() => () => clearTimeout(transcriptPollRef.current), []);
  useEffect(() => () => clearTimeout(importPollRef.current), []);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streaming]);
  useEffect(() => { if (!toast) return; const timeout = setTimeout(() => setToast(''), 2600); return () => clearTimeout(timeout); }, [toast]);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_BASE}/api/config`, { signal: controller.signal })
      .then(response => response.ok ? response.json() : Promise.reject(new Error('API unavailable')))
      .then(config => {
        setApiOnline(true);
        setMaxUploadBytes(config.max_upload_bytes || DEFAULT_MAX_UPLOAD_BYTES);
      })
      .catch(() => setApiOnline(false));
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!urlInput.trim() || importingUrl || previewMode) { setImportHint(null); return undefined; }
    let parsed;
    try {
      parsed = new URL(urlInput.trim());
      if (parsed.protocol !== 'https:') throw new Error('Use a public HTTPS link');
    } catch (error) {
      setImportHint({ supported: false, message: error.message || 'Enter a valid public video link' });
      return undefined;
    }
    setImportHint({ checking: true, message: 'Checking link support…' });
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      apiFetch(`${API_BASE}/api/imports/check`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: parsed.href }), signal: controller.signal
      }).then(async response => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || 'This link cannot be imported');
        setImportHint(result);
      }).catch(error => { if (error.name !== 'AbortError') setImportHint({ supported: false, message: error.message }); });
    }, 450);
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [urlInput, importingUrl, previewMode]);
  useEffect(() => {
    if (page !== 'library') return undefined;
    const timeout = setTimeout(() => { void refreshLibrary(libraryQuery); }, 250);
    return () => clearTimeout(timeout);
  }, [libraryQuery, page]);
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get('share');
    if (!token || handledShareRef.current) return;
    handledShareRef.current = true;
    apiFetch(`${API_BASE}/api/shares/${encodeURIComponent(token)}`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Shared moment unavailable')))
      .then(shared => {
        pendingSeekRef.current = shared.timestamp;
        setVideoUrl(`${API_BASE}${shared.content_url}`);
        setVideoId('');
        activeVideoIdRef.current = '';
        setSourceType('shared');
        setSourceUrl('');
        setFileName(shared.name);
        setDuration(shared.duration || 0);
        setCurrentTime(shared.timestamp || 0);
        setMessages([]);
        setPersistedScenes([]);
        setAnnotations([]);
        setVideoTags([]);
        setOverview({ summary: `A shared moment opens at ${formatTime(shared.timestamp)}.`, tags: [], generated: false });
        setPanel('overview');
        setPage('studio');
      })
      .catch(() => setToast('This shared moment is unavailable or expired'));
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_BASE}/api/videos`, { signal: controller.signal })
      .then(response => response.ok ? response.json() : [])
      .then(setLibraryVideos)
      .catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch(`${API_BASE}/api/imports?active=true&limit=1`, { signal: controller.signal })
      .then(response => response.ok ? response.json() : [])
      .then(items => {
        if (!items[0]) return;
        setImportJob(items[0]);
        setImportingUrl(true);
        void pollImport(items[0].id);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  useEffect(() => {
    function handleShortcut(event) {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key === '/') {
        event.preventDefault();
        openLibrarySearch();
      }
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        videoUrl ? setPage('studio') : fileRef.current?.click();
      }
    }
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [videoUrl]);

  function openLibrarySearch() {
    setPage('library');
    requestAnimationFrame(() => librarySearchRef.current?.focus());
  }

  async function refreshLibrary(query = '') {
    try {
      const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : '';
      const response = await apiFetch(`${API_BASE}/api/videos${suffix}`);
      if (response.ok) setLibraryVideos(await response.json());
    } catch {
      // Keep the last usable library snapshot while the API reconnects.
    }
  }

  async function selectConversation(conversation) {
    if (!videoId && !activeVideoIdRef.current) return;
    const selectedVideoId = videoId || activeVideoIdRef.current;
    setConversationId(conversation.id);
    activeConversationIdRef.current = conversation.id;
    setShowConversations(false);
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${selectedVideoId}/messages?conversation_id=${encodeURIComponent(conversation.id)}`);
      if (!response.ok) throw new Error('History unavailable');
      const history = await response.json();
      if (activeConversationIdRef.current === conversation.id) {
        setMessages(history.map(message => ({ id: message.id, role: message.role, text: message.content })));
      }
    } catch {
      setToast('Conversation history could not be loaded');
    }
  }

  async function loadConversations(id, preferredId = '') {
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/conversations`);
      if (!response.ok) return;
      const items = await response.json();
      if (activeVideoIdRef.current !== id) return;
      setConversations(items);
      const selectedId = preferredId || activeConversationIdRef.current;
      const selected = items.find(item => item.id === selectedId) || items[0];
      if (selected) await selectConversation(selected);
    } catch {
      // A new conversation can still be created when history is temporarily unavailable.
    }
  }

  async function newConversation() {
    if (!videoId) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New conversation' })
      });
      if (!response.ok) throw new Error('Create failed');
      const created = await response.json();
      setConversations(old => [created, ...old]);
      setConversationId(created.id);
      activeConversationIdRef.current = created.id;
      setMessages([]);
      setShowConversations(false);
      setToast('New conversation started');
    } catch {
      setToast('A new conversation could not be started');
    }
  }

  async function renameConversation(event, item) {
    event.preventDefault();
    const title = conversationDraft.trim();
    if (!title) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/conversations/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
      });
      if (!response.ok) throw new Error('Rename failed');
      setConversations(old => old.map(conversation => conversation.id === item.id ? { ...conversation, title } : conversation));
      setEditingConversationId('');
    } catch {
      setToast('The conversation could not be renamed');
    }
  }

  async function deleteConversation(item) {
    if (!videoId) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/conversations/${item.id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Delete failed');
      const remainingConversations = conversations.filter(conversation => conversation.id !== item.id);
      setConversations(remainingConversations);
      setPendingConversationDeleteId('');
      if (conversationId === item.id) {
        setConversationId('');
        activeConversationIdRef.current = '';
        setMessages([]);
        if (remainingConversations[0]) await selectConversation(remainingConversations[0]);
      }
      setToast('Conversation deleted');
    } catch {
      setToast('The conversation could not be deleted');
    }
  }

  async function loadOverview(id) {
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/summary`);
      if (response.ok) setOverview(await response.json());
    } catch {
      // The video remains usable if overview generation is unavailable.
    }
  }

  async function loadTags(id) {
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/tags`);
      if (response.ok && activeVideoIdRef.current === id) setVideoTags(await response.json());
    } catch {
      // Tags are optional organization metadata and do not block the video.
    }
  }

  async function addTag(event) {
    event.preventDefault();
    const tag = tagDraft.trim().toLowerCase();
    if (!videoId || !tag) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Tag failed');
      setVideoTags(old => [...new Set([...old, result.tag])].sort());
      setLibraryVideos(old => old.map(video => video.id === videoId ? { ...video, tags: [...new Set([...(video.tags || []), result.tag])].sort() } : video));
      setTagDraft('');
    } catch {
      setToast('The tag could not be saved');
    }
  }

  async function removeTag(tag) {
    if (!videoId) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/tags/${encodeURIComponent(tag)}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Remove failed');
      setVideoTags(old => old.filter(item => item !== tag));
      setLibraryVideos(old => old.map(video => video.id === videoId ? { ...video, tags: (video.tags || []).filter(item => item !== tag) } : video));
    } catch {
      setToast('The tag could not be removed');
    }
  }

  async function loadAnalysis(id) {
    clearTimeout(analysisPollRef.current);
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/analysis`);
      if (!response.ok) return;
      const result = await response.json();
      if (activeVideoIdRef.current !== id) return;
      const status = result.job?.status || 'queued';
      setAnalysisStatus(status);
      setAnalysisError(result.job?.error || '');
      setProgress(result.job?.progress || 0);
      const sceneItems = await Promise.all((result.scenes || []).map(async scene => {
        let image = '';
        if (scene.thumbnail_url) {
          try { image = await protectedObjectUrl(`${API_BASE}${scene.thumbnail_url}`); } catch { /* keep text-only evidence */ }
        }
        return { ...scene, time: scene.start_seconds, image };
      }));
      setPersistedScenes(sceneItems);
      setProcessing(status === 'queued' || status === 'processing');
      if (status === 'queued' || status === 'processing') {
        analysisPollRef.current = setTimeout(() => loadAnalysis(id), 900);
      } else if (status === 'ready') {
        void refreshLibrary();
      }
    } catch {
      if (activeVideoIdRef.current === id) setProcessing(false);
    }
  }

  async function loadTranscript(id, query = transcriptQuery) {
    clearTimeout(transcriptPollRef.current);
    setTranscriptLoading(true);
    try {
      const parameters = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : '';
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/transcript${parameters}`);
      if (!response.ok) return;
      const result = await response.json();
      if (activeVideoIdRef.current !== id) return;
      const status = result.job?.status || 'queued';
      setTranscriptStatus(status);
      setTranscriptError(result.job?.error || '');
      setTranscriptSegments(result.segments || []);
      setTranscriptChapters(result.chapters || []);
      if (status === 'queued' || status === 'processing') {
        transcriptPollRef.current = setTimeout(() => loadTranscript(id, query), 1200);
      }
    } catch {
      if (activeVideoIdRef.current === id) setTranscriptError('Transcript service is temporarily unavailable');
    } finally {
      if (activeVideoIdRef.current === id) setTranscriptLoading(false);
    }
  }

  async function toggleTranscriptHighlight(segment) {
    if (!videoId) return;
    const method = segment.highlighted ? 'DELETE' : 'POST';
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/transcript/highlights/${segment.id}`, { method });
      if (!response.ok) throw new Error();
      setTranscriptSegments(items => items.map(item => item.id === segment.id ? { ...item, highlighted: !segment.highlighted } : item));
      setToast(segment.highlighted ? 'Highlight removed' : 'Quote highlighted');
    } catch {
      setToast('The highlight could not be updated');
    }
  }

  async function loadAnnotations(id) {
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${id}/annotations`);
      if (response.ok && activeVideoIdRef.current === id) setAnnotations(await response.json());
    } catch {
      // Annotations are optional and do not block the video workspace.
    }
  }

  async function addAnnotation(event) {
    event.preventDefault();
    const note = annotationDraft.trim();
    if (!videoId || !note) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timestamp: currentTime, note })
      });
      if (!response.ok) throw new Error('Annotation failed');
      const annotation = await response.json();
      setAnnotations(old => [...old, annotation].sort((a, b) => a.timestamp - b.timestamp));
      setAnnotationDraft('');
      setToast(`Note saved at ${formatTime(annotation.timestamp)}`);
    } catch {
      setToast('The timestamped note could not be saved');
    }
  }

  async function removeAnnotation(annotationId) {
    if (!videoId) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/annotations/${annotationId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Delete failed');
      setAnnotations(old => old.filter(item => item.id !== annotationId));
      setToast('Note removed');
    } catch {
      setToast('The note could not be removed');
    }
  }

  async function shareCurrentMoment() {
    if (!videoId) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/shares`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timestamp: currentTime, expires_hours: 168 })
      });
      if (!response.ok) throw new Error('Share failed');
      const result = await response.json();
      const url = new URL(result.share_path, window.location.origin).toString();
      setLastShareUrl(url);
      try {
        await navigator.clipboard.writeText(url);
        setToast(`Link copied for ${formatTime(result.timestamp)}`);
      } catch {
        setToast('Share link created');
      }
    } catch {
      setToast('The shared moment could not be created');
    }
  }

  async function downloadPrivate(path, fallbackName) {
    try {
      const response = await apiFetch(`${API_BASE}${path}`);
      if (!response.ok) throw new Error('Download failed');
      const disposition = response.headers.get('content-disposition') || '';
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = match?.[1] || fallbackName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setToast('The private download could not be prepared');
    }
  }

  async function retryTranscript() {
    if (!videoId) return;
    setTranscriptStatus('queued');
    setTranscriptError('');
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/transcript/retry`, { method: 'POST' });
      if (!response.ok) throw new Error('Retry failed');
      void loadTranscript(videoId, transcriptQuery);
    } catch {
      setTranscriptStatus('failed');
      setTranscriptError('The transcript could not be restarted');
    }
  }

  async function retryAnalysis() {
    if (!videoId) return;
    setAnalysisStatus('queued');
    setAnalysisError('');
    setProgress(0);
    setProcessing(true);
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/analysis/retry`, { method: 'POST' });
      if (!response.ok) throw new Error('Retry failed');
      void loadAnalysis(videoId);
    } catch {
      setProcessing(false);
      setAnalysisStatus('failed');
      setAnalysisError('The analysis could not be restarted');
    }
  }

  async function renameStoredVideo(event, videoId) {
    event.preventDefault();
    const name = editingName.trim();
    if (!name) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      if (!response.ok) throw new Error('Rename failed');
      setLibraryVideos(old => old.map(video => video.id === videoId ? { ...video, name } : video));
      setEditingVideoId('');
      setEditingName('');
      setToast('Video renamed');
    } catch {
      setToast('The video could not be renamed');
    }
  }

  async function deleteStoredVideo(videoId) {
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Delete failed');
      setLibraryVideos(old => old.filter(video => video.id !== videoId));
      setPendingDeleteId('');
      if (activeVideoIdRef.current === videoId) {
        activeVideoIdRef.current = '';
        setVideoId('');
        setVideoUrl('');
        setPage('home');
      }
      setToast('Video and its saved data were deleted');
    } catch {
      setToast('The video could not be deleted');
    }
  }

  function toggleComparison(videoId) {
    setCompareSelection(old => old.includes(videoId) ? old.filter(id => id !== videoId) : old.length < 2 ? [...old, videoId] : [old[1], videoId]);
  }

  async function compareSelected() {
    if (compareSelection.length !== 2) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_ids: compareSelection })
      });
      if (!response.ok) throw new Error('Comparison failed');
      setComparison(await response.json());
      setPage('compare');
    } catch {
      setToast('The videos could not be compared');
    }
  }

  function openComparisonMoment(videoId, timestamp = 0) {
    const video = libraryVideos.find(item => item.id === videoId);
    if (!video) return;
    pendingSeekRef.current = timestamp;
    openStoredVideo(video);
  }

  async function openStoredVideo(video, cacheBust = '') {
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
    setVideoUrl('');
    setVideoId(video.id);
    activeVideoIdRef.current = video.id;
    setSourceType(video.source_type || 'upload');
    setSourceUrl(video.source_url || '');
    setFileName(video.name);
    setDuration(video.duration || 0);
    setConversationId('');
    activeConversationIdRef.current = '';
    setConversations([]);
    setShowConversations(false);
    setMessages([]);
    setCurrentTime(0);
    setPlaybackError(false);
    setPersistedScenes([]);
    setTranscriptStatus('queued');
    setTranscriptError('');
    setTranscriptSegments([]);
    setTranscriptChapters([]);
    setTranscriptQuery('');
    setAnnotations([]);
    setAnnotationDraft('');
    setVideoTags(video.tags || []);
    setTagDraft('');
    setLastShareUrl('');
    setAnalysisStatus(video.analysis_status || 'queued');
    setAnalysisError('');
    setProgress(video.analysis_progress || 0);
    setProcessing(video.analysis_status === 'queued' || video.analysis_status === 'processing');
    setOverview({ summary: 'Loading the saved overview…', tags: [], generated: false });
    setPage('studio');
    try {
      const playback = await apiFetch(`${API_BASE}/api/videos/${video.id}/playback${cacheBust ? `?v=${cacheBust}` : ''}`);
      if (!playback.ok) throw new Error('Playback unavailable');
      const descriptor = await playback.json();
      if (descriptor.source === 'private-object-storage') {
        if (activeVideoIdRef.current === video.id) setVideoUrl(descriptor.url);
      } else {
        const objectUrl = await protectedObjectUrl(`${API_BASE}${descriptor.url}`);
        if (activeVideoIdRef.current === video.id) setVideoUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      }
    } catch {
      setPlaybackError(true);
    }
    void loadOverview(video.id);
    void loadAnalysis(video.id);
    void loadConversations(video.id);
    void loadTranscript(video.id, '');
    void loadAnnotations(video.id);
    void loadTags(video.id);
  }

  async function repairRemoteImport() {
    if (!videoId || sourceType !== 'url' || repairingImport) return;
    setRepairingImport(true);
    setToast('Repairing the web import…');
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/${videoId}/reimport`, { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'The web import could not be repaired');
      setLibraryVideos(old => old.map(item => item.id === result.id ? { ...item, ...result } : item));
      openStoredVideo(result, Date.now());
      setToast('Web video repaired and ready');
    } catch (error) {
      setToast(error.message || 'The web import could not be repaired');
    } finally {
      setRepairingImport(false);
    }
  }

  async function pollImport(importId) {
    clearTimeout(importPollRef.current);
    try {
      const response = await apiFetch(`${API_BASE}/api/imports/${importId}`);
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Import status is unavailable');
      setImportJob(result);
      if (result.status === 'ready' && result.video) {
        setImportingUrl(false);
        setApiOnline(true);
        setLibraryVideos(old => [result.video, ...old.filter(item => item.id !== result.video.id)]);
        setUrlInput('');
        openStoredVideo(result.video, Date.now());
        setToast('Online video added to your library');
        return;
      }
      if (result.status === 'failed' || result.status === 'cancelled') {
        setImportingUrl(false);
        setToast(result.error || (result.status === 'cancelled' ? 'Import cancelled' : 'The online video could not be imported'));
        return;
      }
      importPollRef.current = setTimeout(() => { void pollImport(importId); }, 700);
    } catch (error) {
      setImportingUrl(false);
      setToast(error.message || 'Import status is unavailable');
    }
  }

  async function cancelImport() {
    if (!importJob?.id) return;
    try {
      await apiFetch(`${API_BASE}/api/imports/${importJob.id}/cancel`, { method: 'POST' });
      setImportJob(old => old ? { ...old, status: 'cancelling', detail: 'Cancelling import' } : old);
      void pollImport(importJob.id);
    } catch {
      setToast('The import could not be cancelled');
    }
  }

  async function retryImport() {
    if (!importJob?.id) return;
    setImportingUrl(true);
    try {
      const response = await apiFetch(`${API_BASE}/api/imports/${importJob.id}/retry`, { method: 'POST' });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Retry failed');
      setImportJob(result);
      void pollImport(importJob.id);
    } catch (error) {
      setImportingUrl(false);
      setToast(error.message || 'The import could not be retried');
    }
  }

  async function importFromUrl(event) {
    event.preventDefault();
    if (previewMode) { onRequestSignIn(); return; }
    const value = urlInput.trim();
    if (!value || importingUrl) return;
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== 'https:') throw new Error('Use a public HTTPS video link');
    } catch (error) {
      setToast(error.message || 'Enter a valid public video link');
      return;
    }
    setImportingUrl(true);
    setToast('Importing the public video…');
    try {
      const response = await apiFetch(`${API_BASE}/api/imports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: value })
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'The online video could not be imported');
      setApiOnline(true);
      setImportJob(result);
      setToast('Import started — you can leave this page');
      void pollImport(result.id);
    } catch (error) {
      setToast(error.message || 'The online video could not be imported');
      setImportingUrl(false);
    }
  }

  async function sendUpload(file) {
    const form = new FormData();
    form.append('file', file);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    let reachedApi = false;
    try {
      const response = await apiFetch(`${API_BASE}/api/videos/upload`, { method: 'POST', body: form, signal: controller.signal });
      reachedApi = true;
      setApiOnline(true);
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || 'Upload failed');
      }
      const uploaded = await response.json();
      setVideoId(uploaded.id);
      activeVideoIdRef.current = uploaded.id;
      setConversations([]);
      setConversationId('');
      activeConversationIdRef.current = '';
      setTranscriptStatus(uploaded.source_type === 'demo' ? 'not_applicable' : 'queued');
      setTranscriptSegments([]);
      setTranscriptChapters([]);
      setTranscriptQuery('');
      setAnnotations([]);
      setAnnotationDraft('');
      setLastShareUrl('');
      setLibraryVideos(old => [uploaded, ...old.filter(item => item.id !== uploaded.id)]);
      setApiOnline(true);
      await loadOverview(uploaded.id);
      void loadAnalysis(uploaded.id);
      void loadTranscript(uploaded.id, '');
      void loadAnnotations(uploaded.id);
      setToast('Video safely added to your library');
      return uploaded;
    } catch (error) {
      setVideoId('');
      activeVideoIdRef.current = '';
      setProcessing(false);
      setApiOnline(reachedApi);
      setToast(reachedApi ? error.message : 'Video opened locally · analysis is unavailable');
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function loadFile(file, { demo = false } = {}) {
    if (!file || !file.type.startsWith('video/')) {
      setToast('Choose an MP4, MOV, WebM, or AVI video');
      return;
    }
    if (file.size > maxUploadBytes) {
      setToast(`That video is larger than the ${formattedUploadLimit} limit`);
      return;
    }
    clearTimeout(analysisPollRef.current);
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
    setVideoUrl(demo ? DEMO_VIDEO_PATH : URL.createObjectURL(file));
    setSourceType(demo ? 'demo' : 'upload');
    setSourceUrl('');
    setFileName(demo ? 'Alpine trail — demo.webm' : file.name);
    setVideoId('');
    activeVideoIdRef.current = '';
    setConversationId('');
    activeConversationIdRef.current = '';
    setConversations([]);
    setOverview(demo ? DEMO_OVERVIEW : {
      summary: 'This video is ready locally. Connect the API and a vision model to generate a grounded visual overview.',
      tags: [],
      generated: false
    });
    setPlaybackError(false);
    setMessages([]);
    setCurrentTime(0);
    setPersistedScenes([]);
    setVideoTags([]);
    setTagDraft('');
    setTranscriptStatus(demo ? 'not_applicable' : 'queued');
    setTranscriptError('');
    setTranscriptSegments([]);
    setTranscriptChapters([]);
    setTranscriptQuery('');
    setAnalysisStatus(demo ? 'ready' : 'uploading');
    setAnalysisError('');
    setProcessing(!demo);
    setProgress(demo ? 100 : 5);
    setPage('studio');
    if (!demo) void sendUpload(file);
  }

  async function loadDemo() {
    setToast('Loading the demo film…');
    try {
      const response = await nativeFetch(DEMO_VIDEO_PATH);
      if (!response.ok) throw new Error('Demo unavailable');
      const blob = await response.blob();
      await loadFile(new File([blob], 'alpine-trail-demo.webm', { type: 'video/webm' }), { demo: true });
    } catch {
      setToast('The demo film could not be loaded');
    }
  }

  function seekTo(seconds) {
    if (!videoRef.current) return;
    videoRef.current.currentTime = Math.min(seconds, videoRef.current.duration || seconds);
    videoRef.current.play().catch(() => {});
  }

  function completeClientReply(question) {
    const reply = makeReply(question, duration, sourceType === 'demo');
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
        let streamedConversationId = conversationId;
        const response = await apiFetch(`${API_BASE}/api/chat/stream`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ video_id: videoId, message: question, conversation_id: conversationId || undefined })
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
            if (data.type === 'meta') {
              setConversationId(data.conversation_id);
              activeConversationIdRef.current = data.conversation_id;
              streamedConversationId = data.conversation_id;
            }
            if (data.type === 'token') {
              shown += data.text;
              setMessages(old => [...old.filter(item => item.id !== 'stream'), { id: 'stream', role: 'assistant', text: shown }]);
            }
          }
        }
        setMessages(old => old.map(item => item.id === 'stream' ? { ...item, id: crypto.randomUUID() } : item));
        setStreaming(false);
        void loadConversations(videoId, streamedConversationId);
        return;
      } catch {
        setApiOnline(false);
        setToast(sourceType === 'demo' ? 'Connection changed · using the local demo' : 'Connection lost · no answer was invented');
      }
    }
    completeClientReply(question);
  }

  const uploadDropHandlers = {
    onDragOver: e => { e.preventDefault(); setDragging(true); },
    onDragLeave: () => setDragging(false),
    onDrop: e => { e.preventDefault(); setDragging(false); loadFile(e.dataTransfer.files[0]); }
  };
  const openUpload = () => previewMode ? onRequestSignIn() : fileRef.current?.click();
  const openLibrary = () => previewMode ? onRequestSignIn() : setPage('library');

  return <div className="app-shell">
    <aside className="side-rail">
      <Logo />
      <nav className="main-nav">
        <button className={page === 'home' ? 'active' : ''} onClick={() => setPage('home')}><Home size={17} /><span>Home</span></button>
        <button className={page === 'studio' ? 'active' : ''} onClick={() => videoUrl ? setPage('studio') : fileRef.current?.click()}><MessageSquareText size={17} /><span>Video chat</span><kbd>⌘K</kbd></button>
        <button className={page === 'library' ? 'active' : ''} onClick={openLibrary}><Library size={17} /><span>Library</span></button>
      </nav>
      <div className="rail-spacer" />
      <span className="maker-note">Made by Aarav</span>
    </aside>

    <div className="app-main">
      <header className="topbar">
        <div className="mobile-brand"><Logo /></div>
        <button className="search-command" onClick={openLibrarySearch}><Search size={15} /><span>Search your videos</span><kbd>⌘ /</kbd></button>
          <div className="top-actions"><span className={`system-status ${apiOnline ? '' : 'offline'}`}><i />{previewMode ? 'Interactive demo' : apiOnline ? 'API connected' : 'Demo mode'}</span><button className="icon-button" onClick={() => previewMode ? onRequestSignIn() : setPage('library')} aria-label="Open recent videos"><Clock3 size={17} /></button><button className="new-video" onClick={() => { if (previewMode) onRequestSignIn(); else { setPage('home'); requestAnimationFrame(() => urlInputRef.current?.focus()); } }}><Plus size={15} />{previewMode ? 'Sign in to add videos' : 'Add video'}</button></div>
      </header>

      {page === 'home' && <main className="home-page">
        <section className="hero-grid">
          <div className="hero-copy">
            <span className="hero-pill">Video understanding, made simple</span>
            <h1>Your video.<br /><em>Ready to answer.</em></h1>
            <p>Upload a video and have a grounded conversation about what happens inside it. Ask follow-ups, understand key moments, and jump to exact timestamps.</p>
            <div className="hero-actions"><button className="primary-cta" onClick={openUpload}><UploadCloud size={17} />Upload a video</button><button className="ghost-cta" onClick={() => previewMode ? onRequestSignIn() : urlInputRef.current?.focus()}><Link2 size={15} />Use a link</button><button className="ghost-cta" onClick={loadDemo}><Play size={15} fill="currentColor" />Try demo</button></div>
            <div className="trust-row"><span><Check size={12} />Video understanding only</span><span><Check size={12} />No video generation</span><span><Check size={12} />Evidence-linked answers</span></div>
          </div>
          <button className="hero-film" onClick={loadDemo} aria-label="Play demo video">
            <img src="/demo/trail-runner.png" alt="Trail runner overlooking a misty mountain valley" />
            <div className="film-top"><span>DEMO FILM</span><b>00:12</b></div>
            <div className="play-orb"><Play size={20} fill="currentColor" /></div>
            <div className="film-caption"><div><small>INCLUDED DEMO FOOTAGE</small><strong>Across the alpine valley</strong></div><span>4 scenes</span></div>
          </button>
        </section>

        <form className="url-import" onSubmit={importFromUrl}>
          <Link2 size={19} />
          <label htmlFor="video-url"><strong>Analyze a public video link</strong><span>YouTube, Instagram, TikTok, Vimeo, X, Facebook, Twitch, Reddit, or a direct video URL</span></label>
          <input id="video-url" ref={urlInputRef} type="url" inputMode="url" value={urlInput} onChange={event => setUrlInput(event.target.value)} placeholder="https://…" disabled={importingUrl} />
          <button type="submit" disabled={!urlInput.trim() || importingUrl}>{importingUrl ? 'Importing…' : 'Import link'} <ArrowRight size={14} /></button>
        </form>
        {importHint && !importJob && <div className={`import-hint ${importHint.supported === false ? 'unsupported' : ''}`}><strong>{importHint.platform || (importHint.checking ? 'Checking this link' : 'Link support')}</strong><span>{importHint.message}</span></div>}
        {importJob && importJob.status !== 'ready' && <div className="import-status"><span><strong>{importJob.platform ? `${importJob.platform} · ` : ''}{importJob.detail || 'Preparing import'}</strong><small>{importJob.status === 'failed' ? importJob.error : importJob.status === 'cancelled' ? 'The download was stopped.' : `${importJob.progress || 0}% complete${importMetrics(importJob) ? ` · ${importMetrics(importJob)}` : ''}`}</small></span>{['queued', 'processing', 'cancelling'].includes(importJob.status) ? <><i><em style={{ width: `${importJob.progress || 0}%` }} /></i><button onClick={cancelImport} disabled={importJob.status === 'cancelling'}>{importJob.status === 'cancelling' ? 'Cancelling…' : 'Cancel'}</button></> : <><button onClick={retryImport}>Retry</button><button onClick={() => setImportJob(null)}>Dismiss</button></>}</div>}

        <section className={`quick-upload ${dragging ? 'dragging' : ''}`} {...uploadDropHandlers}>
          <div className="upload-badge"><UploadCloud size={20} /></div>
          <div><strong>Drop a video to ask questions about it</strong><span>MP4, MOV, WebM or AVI · up to {formattedUploadLimit}</span></div>
          <button onClick={openUpload}>Browse files <ArrowRight size={14} /></button>
        </section>

        <section className="dashboard-row">
          <div className="recent-section">
            <div className="section-title"><div><span>YOUR SPACE</span><h2>Recent videos</h2></div><button onClick={openLibrary}>View library <ArrowRight size={13} /></button></div>
            <div className="media-grid">
              <button className="media-card featured" onClick={loadDemo}><div className="thumb"><img src="/demo/scene-runner.jpg" alt="Runner scene thumbnail" /><span className="duration">00:12</span><span className="thumb-play"><Play size={13} fill="currentColor" /></span></div><div className="media-info"><strong>Across the alpine valley</strong><span><i />Analyzed · 4 scenes</span></div></button>
              <button className="media-card add-card" onClick={openUpload}><div><Plus size={21} /></div><strong>Analyze your own video</strong><span>Upload, understand, and ask anything</span></button>
            </div>
          </div>
          <aside className="insight-card"><span>HOW IT WORKS</span><h3>Every frame becomes searchable context.</h3><p>Scene mapping, timeline-aware answers, and conversation memory work together in one focused space.</p><div className="metric-row"><div><b>4</b><small>scene types</small></div><div><b>&lt;1s</b><small>timestamp jump</small></div><div><b>∞</b><small>follow-ups</small></div></div></aside>
        </section>
      </main>}

      {page === 'library' && <main className="library-page">
        <div className="page-heading"><div><span className="overline">VIDEO LIBRARY</span><h1>Your visual knowledge base</h1><p>Search, revisit, and continue every video conversation.</p></div><button className="primary-cta" onClick={() => fileRef.current?.click()}><Plus size={16} />Analyze video</button></div>
        <div className="library-toolbar"><div className="library-search"><Search size={15} /><input ref={librarySearchRef} value={libraryQuery} onChange={event => setLibraryQuery(event.target.value)} placeholder="Search title, topic, or transcript" /></div>{compareSelection.length > 0 && <button onClick={compareSelected} disabled={compareSelection.length !== 2}><Layers3 size={15} />{compareSelection.length === 2 ? 'Compare selected' : 'Choose one more'}</button>}<button onClick={() => setLibraryQuery('')}><Grid2X2 size={15} />All videos</button></div>
        <div className="library-grid">
          {showDemoInLibrary && <button className="library-video" onClick={loadDemo}><div><img src="/demo/trail-runner.png" alt="Mountain valley demo" /><span><Play size={15} fill="currentColor" /></span></div><strong>Across the alpine valley</strong><small>Demo · 4 scenes · 12 seconds</small></button>}
          {filteredLibraryVideos.map(video => <article className="library-video stored-video" key={video.id}>
            <button className="library-open" onClick={() => openStoredVideo(video)}>
              <div className="stored-video-preview">{video.poster_url ? <ProtectedImage src={`${API_BASE}${video.poster_url}`} alt="" /> : <FileVideo2 size={27} />}</div>
              <strong>{video.name}</strong>
              <small>{video.source_type === 'url' ? 'Online link' : 'Upload'} · {video.duration ? formatTime(video.duration) : 'duration unavailable'} · {analysisLabel(video)}</small>
              {video.tags?.length > 0 && <span className="library-tags">{video.tags.join(' · ')}</span>}
            </button>
            {editingVideoId === video.id ? <form className="video-rename" onSubmit={event => renameStoredVideo(event, video.id)}><input autoFocus value={editingName} onChange={event => setEditingName(event.target.value)} maxLength={180} aria-label={`Rename ${video.name}`} /><button type="submit">Save</button><button type="button" onClick={() => setEditingVideoId('')}>Cancel</button></form> : <div className="video-actions"><button onClick={() => toggleComparison(video.id)}>{compareSelection.includes(video.id) ? 'Selected' : 'Compare'}</button><button onClick={() => { setEditingVideoId(video.id); setEditingName(video.name); setPendingDeleteId(''); }}>Rename</button>{pendingDeleteId === video.id ? <><button className="delete-confirm" onClick={() => deleteStoredVideo(video.id)}>Confirm delete</button><button onClick={() => setPendingDeleteId('')}>Cancel</button></> : <button onClick={() => { setPendingDeleteId(video.id); setEditingVideoId(''); }}>Delete</button>}</div>}
          </article>)}
          {!showDemoInLibrary && filteredLibraryVideos.length === 0 && <p className="library-empty">No videos match “{libraryQuery}”.</p>}
          <button className="library-upload" onClick={() => fileRef.current?.click()}><UploadCloud size={22} /><strong>Upload another video</strong><small>Build your searchable library</small></button>
        </div>
      </main>}

      {page === 'compare' && comparison && <main className="compare-page">
        <div className="page-heading"><div><span className="overline">VIDEO COMPARISON</span><h1>What changed between them</h1><p>{comparison.summary}</p></div><button className="text-action" onClick={() => setPage('library')}>Back to library</button></div>
        <div className="compare-headings">{comparison.videos.map(video => <button key={video.id} onClick={() => openComparisonMoment(video.id)}><strong>{video.name}</strong><span>{formatTime(video.duration)} · {video.scene_count} moments · {video.transcript_segment_count} transcript lines</span></button>)}</div>
        <div className="compare-moments">{comparison.paired_moments.map(moment => <div key={moment.position}>{[moment.first, moment.second].map((scene, index) => scene ? <button key={index} onClick={() => openComparisonMoment(comparison.videos[index].id, scene.start_seconds)}><span>{formatTime(scene.start_seconds)}</span><strong>{scene.label}</strong><small>{scene.detection_method.replaceAll('-', ' ')}</small></button> : <span key={index}>No matching saved moment</span>)}</div>)}</div>
        <p className="compare-status">{comparison.semantic_status}</p>
      </main>}

      {page === 'studio' && <main className="studio-page">
        <section className="studio-video">
          <div className="studio-heading"><div><span className="overline">VIDEO UNDERSTANDING</span><h1>{fileName}</h1></div><div className="studio-meta"><span><i />{processing ? `Mapping moments · ${progress}%` : analysisStatus === 'failed' ? 'Timeline needs retry' : 'Ready for questions'}</span><button onClick={openUpload}><Plus size={14} />{previewMode ? 'Use your video' : 'Replace'}</button></div></div>
          <div className={`video-frame ${analysisStatus === 'failed' ? 'failed' : ''}`}>
            {videoUrl ? <video ref={videoRef} src={videoUrl} poster={sourceType === 'demo' ? '/demo/trail-runner.png' : undefined} controls onError={() => setPlaybackError(true)} onTimeUpdate={e => setCurrentTime(e.currentTarget.currentTime)} onLoadedMetadata={e => { setPlaybackError(false); setDuration(e.currentTarget.duration || 12); if (pendingSeekRef.current !== null) { e.currentTarget.currentTime = Math.min(pendingSeekRef.current, e.currentTarget.duration || pendingSeekRef.current); setCurrentTime(pendingSeekRef.current); pendingSeekRef.current = null; } }} /> : <button className="empty-player" onClick={() => fileRef.current?.click()}><UploadCloud size={26} /><strong>Choose a video</strong></button>}
            {playbackError && <div className="playback-error"><FileVideo2 size={22} /><strong>{sourceType === 'url' ? 'This web import needs repair' : 'This video codec cannot play in your browser'}</strong><p>{sourceType === 'url' ? 'The earlier download did not include a usable video track. Re-import it from the original link.' : 'MP4 (H.264) and WebM provide the best browser playback support.'}</p><button onClick={sourceType === 'url' ? repairRemoteImport : () => fileRef.current?.click()} disabled={repairingImport}>{sourceType === 'url' ? repairingImport ? 'Repairing…' : 'Repair web import' : 'Choose another file'}</button></div>}
            {processing && <div className="analysis-overlay"><div className="scan-line" /><span className="analysis-orb"><Sparkles size={22} /></span><strong>Preparing the video timeline</strong><p>Extracting evidence frames without guessing what they show</p><div className="analysis-progress"><i style={{ width: `${progress}%` }} /></div><small>{progress}% complete</small></div>}
          </div>
          {!processing && videoUrl && analysisStatus !== 'failed' && duration > 0 && <div className="scene-map">
            <div className="scene-map-head"><div><span className="overline">{sourceType === 'demo' ? 'SCENE MAP' : 'TIMELINE'}</span><strong>{sourceType === 'demo' ? `${scenes.length} moments found` : persistedScenes.length ? `${persistedScenes.length} ${persistedScenes.some(scene => scene.detection_method === 'histogram-cut-v1') ? 'detected moments' : 'evidence frames'}` : 'Reference points'}</strong></div><span>{formatTime(currentTime)} / {formatTime(duration)}</span></div>
            <div className="timeline"><i style={{ width: `${duration ? currentTime / duration * 100 : 0}%` }} />{scenes.map((scene, index) => <button key={scene.label} onClick={() => seekTo(scene.time)} style={{ left: `${duration ? scene.time / duration * 100 : 0}%` }}><span>{index + 1}</span></button>)}</div>
            <div className={`scene-cards ${scenes.some(scene => scene.image) ? '' : 'text-only'}`}>{scenes.map((scene, index) => <button key={scene.id || scene.label} onClick={() => seekTo(scene.time)} className={currentTime >= scene.time && currentTime < (scenes[index + 1]?.time ?? Infinity) ? 'active' : ''}>{scene.image && <img src={scene.image} alt="" />}<div><span>{formatTime(scene.time)}</span><strong>{scene.label}</strong></div></button>)}</div>
          </div>}
        </section>

        <aside className="ai-panel">
          <div className="panel-tabs"><button className={panel === 'ask' ? 'active' : ''} onClick={() => setPanel('ask')}>Ask</button><button className={panel === 'overview' ? 'active' : ''} onClick={() => setPanel('overview')}>Overview</button><button className={panel === 'transcript' ? 'active' : ''} onClick={() => setPanel('transcript')}>Transcript</button><button className="panel-close" onClick={() => setPage('home')} aria-label="Close panel"><PanelLeftClose size={16} /></button></div>
          <div className="panel-content">
            {panel === 'ask' && videoId && analysisStatus !== 'failed' && <div className="conversation-control">
              <button className="conversation-current" onClick={() => setShowConversations(old => !old)}><span>CONVERSATION</span><strong>{currentConversation?.title || 'New conversation'}</strong></button>
              <button className="conversation-new" onClick={newConversation}>New</button>
              {showConversations && <div className="conversation-list">{conversations.length === 0 ? <span>No saved conversations yet</span> : conversations.map(item => editingConversationId === item.id ? <form key={item.id} onSubmit={event => renameConversation(event, item)}><input autoFocus value={conversationDraft} onChange={event => setConversationDraft(event.target.value)} maxLength={120} aria-label={`Rename ${item.title}`} /><button type="submit">Save</button><button type="button" onClick={() => setEditingConversationId('')}>Cancel</button></form> : <div key={item.id} className={item.id === conversationId ? 'active' : ''}><button onClick={() => selectConversation(item)}><strong>{item.title}</strong><small>{item.message_count} messages</small></button>{pendingConversationDeleteId === item.id ? <><button className="conversation-rename" onClick={() => deleteConversation(item)}>Confirm</button><button className="conversation-rename" onClick={() => setPendingConversationDeleteId('')}>Cancel</button></> : <><button className="conversation-rename" onClick={() => { setEditingConversationId(item.id); setConversationDraft(item.title); setPendingConversationDeleteId(''); }}>Rename</button><button className="conversation-rename" onClick={() => { setPendingConversationDeleteId(item.id); setEditingConversationId(''); }}>Delete</button></>}</div>)}</div>}
            </div>}
            {panel === 'transcript' ? <div className="transcript-panel"><span className="ai-chip">TRANSCRIPT</span><h2>Search what was said</h2><form onSubmit={event => { event.preventDefault(); if (videoId) void loadTranscript(videoId, transcriptQuery); }}><Search size={14} /><input value={transcriptQuery} onChange={event => setTranscriptQuery(event.target.value)} placeholder="Search transcript" disabled={!videoId} /><button type="submit" disabled={!videoId || transcriptLoading}>{transcriptLoading ? 'Searching…' : 'Search'}</button>{transcriptQuery && <button type="button" onClick={() => { setTranscriptQuery(''); if (videoId) void loadTranscript(videoId, ''); }}>Clear</button>}</form>{transcriptChapters.length > 0 && !transcriptQuery && <div className="transcript-chapters"><span>CHAPTERS</span>{transcriptChapters.map(chapter => <button key={chapter.id} onClick={() => seekTo(chapter.start_seconds)}><time>{formatTime(chapter.start_seconds)}</time>{chapter.title}</button>)}</div>}{transcriptStatus === 'ready' && transcriptSegments.length > 0 ? <div className="transcript-results">{transcriptSegments.map(segment => <div key={segment.id} className={segment.highlighted ? 'highlighted' : ''}><button onClick={() => seekTo(segment.start_seconds)}><span>{formatTime(segment.start_seconds)}{segment.speaker ? ` · ${segment.speaker}` : ''}</span><p>{segment.text}</p></button><button onClick={() => toggleTranscriptHighlight(segment)}>{segment.highlighted ? 'Remove highlight' : 'Highlight quote'}</button></div>)}</div> : transcriptStatus === 'ready' && transcriptQuery ? <p className="transcript-empty">No transcript lines match “{transcriptQuery}”.</p> : transcriptStatus === 'queued' || transcriptStatus === 'processing' ? <p className="transcript-empty">Preparing the searchable transcript…</p> : transcriptStatus === 'not_configured' ? <div className="transcript-status"><strong>Local transcription needs attention</strong><p>{transcriptError || 'Install the local speech models or configure another transcription provider.'}</p></div> : transcriptStatus === 'no_audio' || transcriptStatus === 'no_speech' || transcriptStatus === 'not_applicable' ? <div className="transcript-status"><strong>No spoken transcript is available</strong><p>{transcriptError || 'This video does not contain detectable speech.'}</p></div> : transcriptStatus === 'failed' ? <div className="transcript-status"><strong>Transcription stopped</strong><p>{transcriptError}</p><button onClick={retryTranscript}>Retry transcript</button></div> : <p className="transcript-empty">Open a saved video to search its transcript.</p>}</div> : panel === 'overview' ? <div className="overview-panel"><span className="ai-chip">OVERVIEW</span><h2>{overview.generated ? 'What the video shows' : 'Analysis status'}</h2><p>{overview.summary}</p><div className="overview-block"><span>VIDEO DETAILS</span><ul><li><Video size={14} />{fileName}</li><li><Clock3 size={14} />{formatTime(duration)} duration</li><li><ImageIcon size={14} />{overview.generated ? 'Grounded demo overview' : persistedScenes.length ? `${persistedScenes.length} evidence frames ready` : 'Semantic analysis needs a vision model'}</li></ul></div>{overview.tags?.length > 0 && <div className="topic-tags">{overview.tags.map(tag => <span key={tag}>{tag}</span>)}</div>}{videoId && <><div className="video-tag-section"><span>LIBRARY TAGS</span><form onSubmit={addTag}><input value={tagDraft} onChange={event => setTagDraft(event.target.value)} placeholder="Add a searchable tag" maxLength={40} /><button type="submit" disabled={!tagDraft.trim()}>Add</button></form>{videoTags.length > 0 && <div>{videoTags.map(tag => <button key={tag} onClick={() => removeTag(tag)} title={`Remove ${tag}`}>{tag} ×</button>)}</div>}</div><div className="overview-actions"><button onClick={shareCurrentMoment}><Link2 size={12} />Share {formatTime(currentTime)}</button><button onClick={() => downloadPrivate("/api/videos/" + videoId + "/clip?start=" + Math.max(0, currentTime - 5) + "&duration=30", fileName + "-clip.mp4")}><Download size={12} />Download 30s clip</button><button onClick={() => downloadPrivate("/api/videos/" + videoId + "/export?format=html&template=brief", fileName + "-report.html")}><Download size={12} />Export report</button>{lastShareUrl && <a href={lastShareUrl}>Open shared link</a>}</div><div className="annotation-section"><span>TIMESTAMPED NOTES</span><form onSubmit={addAnnotation}><input value={annotationDraft} onChange={event => setAnnotationDraft(event.target.value)} placeholder={`Add a note at ${formatTime(currentTime)}`} maxLength={1000} /><button type="submit" disabled={!annotationDraft.trim()}>Save</button></form>{annotations.length === 0 ? <p>No notes yet.</p> : annotations.map(annotation => <div key={annotation.id}><button onClick={() => seekTo(annotation.timestamp)}>{formatTime(annotation.timestamp)}</button><p>{annotation.note}</p><button onClick={() => removeAnnotation(annotation.id)}>Remove</button></div>)}</div></>}</div> :
            processing ? <div className="panel-empty"><span className="ai-loader"><span>•••</span></span><h2>Building the timeline</h2><p>This job is saved, so you can leave and return while the evidence frames are prepared.</p></div> : analysisStatus === 'failed' ? <div className="panel-empty focused-failure"><span className="ai-chip">IMPORT ISSUE</span><h2>{sourceType === 'url' ? 'Repair this web video' : 'Timeline extraction stopped'}</h2><p>{sourceType === 'url' ? 'The saved download is missing a playable video track. The original link is still available.' : analysisError || 'The video could not be decoded.'}</p><button onClick={sourceType === 'url' ? repairRemoteImport : retryAnalysis} disabled={repairingImport}>{sourceType === 'url' ? repairingImport ? 'Repairing…' : 'Repair web import' : 'Retry timeline'}</button>{sourceType === 'url' && sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">Open original link</a>}</div> : messages.length === 0 ? <div className="panel-ready"><span className="ai-chip">VIDEO READY</span><h2>What do you want to know?</h2><p>{sourceType === 'demo' || videoId ? 'Ask about a scene, a change, or a detail. Grounded answers link back to the moment.' : 'The video is open locally. Connect the API and a vision model for grounded answers.'}</p><div className="suggestion-list">{promptSuggestions.map(prompt => <button key={prompt} onClick={() => ask(prompt)}><span>{prompt}</span><ArrowUp size={13} /></button>)}</div><div className="mini-overview"><div>{sourceType === 'demo' ? <img src="/demo/scene-waterfall.jpg" alt="Waterfall scene" /> : persistedScenes[0]?.image && <img src={persistedScenes[0].image} alt="" />}<span><strong>{sourceType === 'demo' ? '4 moments mapped' : persistedScenes.length ? `${persistedScenes.length} evidence frames` : 'Timeline available'}</strong><small>{sourceType === 'demo' ? 'Landscape · movement · light' : persistedScenes.length ? 'Extracted from this video' : 'Semantic analysis needs a vision model'}</small></span></div><button onClick={() => setPanel('overview')}>Open overview <ChevronRight size={12} /></button></div></div> :
            <div className="messages">{messages.map((message, index) => <div key={message.id || index} className={`message ${message.role}`}>{message.role === 'assistant' && <span className="assistant-avatar">U</span>}<div><span className="message-author">{message.role === 'assistant' ? 'UNDERSTAND' : 'YOU'}</span><p>{message.role === 'assistant' ? <TimestampText text={message.text} onSeek={seekTo} /> : message.text}</p></div></div>)}{streaming && <span className="thinking"><i />Reading the scene…</span>}<div ref={chatEndRef} /></div>}
          </div>
          {panel === 'ask' && analysisStatus !== 'failed' && <div className="composer-area"><div className={`composer ${processing || !videoUrl ? 'disabled' : ''}`}><textarea rows={1} value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder="Ask anything about this video…" disabled={processing || !videoUrl} /><button onClick={() => ask()} disabled={!input.trim() || streaming}><ArrowUp size={16} /></button></div><span><Sparkles size={10} />Answers link back to the exact moment</span></div>}
        </aside>
      </main>}
    </div>

    <input ref={fileRef} type="file" hidden accept="video/mp4,video/quicktime,video/webm,video/x-msvideo" onClick={e => { e.currentTarget.value = ''; }} onChange={e => { const selected = e.target.files?.[0]; if (selected) loadFile(selected); }} />
    {toast && <div className="toast"><Check size={14} />{toast}</div>}
  </div>;
}

function App() {
  const [session, setSession] = useState(undefined);
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [previewingDemo, setPreviewingDemo] = useState(false);

  useEffect(() => {
    if (!supabase) {
      setSession(null);
      return undefined;
    }
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => setSession(nextSession));
    return () => data.subscription.unsubscribe();
  }, []);

  async function requestSignIn(event) {
    event.preventDefault();
    const address = email.trim();
    if (!address || !supabase) return;
    setSending(true);
    setMessage('');
    const { error } = await supabase.auth.signInWithOtp({
      email: address,
      options: { emailRedirectTo: window.location.origin }
    });
    setSending(false);
    setMessage(error ? error.message : 'Check your email for the secure sign-in link.');
  }

  if (supabase && session === undefined) return <div className="auth-page"><Logo /><p>Opening your workspace…</p></div>;
  if (supabase && !session && previewingDemo) return <><WorkspaceApp previewMode onRequestSignIn={() => setPreviewingDemo(false)} /><button className="preview-signin" onClick={() => setPreviewingDemo(false)}>Sign in to use your videos <ArrowRight size={13} /></button></>;
  if (supabase && !session) return <main className="auth-page"><Logo /><div className="auth-layout"><div className="auth-copy"><span>PRIVATE VIDEO WORKSPACE</span><h1>Understand a video before you sign in.</h1><p>Explore the complete 12-second demo, ask grounded questions, and jump between evidence-linked moments. Create an account only when you are ready to add your own videos.</p><button className="demo-entry" type="button" onClick={() => setPreviewingDemo(true)}><Play size={14} fill="currentColor" />Try the interactive demo</button><form onSubmit={requestSignIn}><label htmlFor="sign-in-email">Email address</label><input id="sign-in-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="you@example.com" required /><button type="submit" disabled={sending}>{sending ? 'Sending…' : 'Email me a sign-in link'} <ArrowRight size={14} /></button></form>{message && <p className="auth-message">{message}</p>}</div><ol className="onboarding-steps"><li><span>01</span><strong>Open the demo</strong><p>See the real player, timeline, transcript, and question panel.</p></li><li><span>02</span><strong>Ask a question</strong><p>Try the suggested prompts and follow the answer back to its timestamp.</p></li><li><span>03</span><strong>Add your video</strong><p>Sign in when you want private uploads, public-link imports, and a saved library.</p></li></ol></div><div className="legal-links"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a></div></main>;
  return <><WorkspaceApp />{supabase && <button className="account-signout" onClick={() => supabase.auth.signOut()}>Sign out</button>}<div className="legal-links app-legal-links"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a></div></>;
}

const rootElement = document.getElementById('root');
window.__understandVideoRoot ||= createRoot(rootElement);
window.__understandVideoRoot.render(<App />);
