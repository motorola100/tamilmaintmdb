let cachedItems = [];
const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', () => {
  $('searchBtn')?.addEventListener('click', searchItems);
  $('searchInput')?.addEventListener('keydown', e => { if (e.key === 'Enter') searchItems(); });
  $('movieModal')?.addEventListener('click', e => { if (e.target.id === 'movieModal') closeModal(); });
  refreshItems();
  window.setInterval(refreshItems, 10000);
});

async function refreshItems() {
  try {
    const [itemsResponse, statusResponse] = await Promise.all([
      fetch('/api/movies', {cache: 'no-store'}), fetch('/api/status', {cache: 'no-store'})
    ]);
    const itemsData = await itemsResponse.json();
    const statusData = await statusResponse.json();
    if (!itemsResponse.ok) throw new Error(itemsData.error || 'Could not load database items');
    cachedItems = itemsData.movies || [];
    $('movieCount').textContent = `${cachedItems.length} items stored`;
    renderItems(cachedItems, 'movieGrid');
    $('movieTotal').textContent = `${cachedItems.length} items`;
    $('movieListContainer').style.display = cachedItems.length ? 'block' : 'none';
    $('loadingContainer').style.display = statusData.is_loading ? 'block' : 'none';
    const error = statusData.last_error || statusData.database_error;
    setStatus(error ? 'error' : (statusData.is_loading ? 'loading' : 'success'), error ? `Issue: ${error}` : statusData.is_loading ? 'Checking for new releases…' : `Automatic checking enabled${statusData.last_update ? ' · Last check: ' + new Date(statusData.last_update).toLocaleTimeString() : ''}`);
  } catch (err) {
    console.error(err);
    setStatus('error', `Could not load items: ${err.message}`);
  }
}

function setStatus(type, message) {
  if ($('statusText')) $('statusText').textContent = message;
  if ($('statusDot')) $('statusDot').className = `status-dot ${type}`;
}
function renderItems(items, targetId) {
  const target = $(targetId);
  if (!target) return;
  target.innerHTML = items.map(item => {
    const type = item.media_type === 'tv' ? 'Series' : item.media_type === 'movie' ? 'Movie' : 'Item';
    const season = item.season_start != null ? ` · S${item.season_start}${item.season_end && item.season_end !== item.season_start ? `–S${item.season_end}` : ''}` : '';
    const episode = item.episode_start != null ? ` · E${item.episode_start}${item.episode_end !== item.episode_start ? `–${item.episode_end}` : ''}` : '';
    return `<article class="movie-card" tabindex="0" role="button" onclick="getItemDetails(${Number(item.id)})" onkeydown="if(event.key==='Enter')getItemDetails(${Number(item.id)})">
      <div class="card-art">${item.poster ? `<img src="${attr(item.poster)}" alt="" loading="lazy" onerror="this.parentElement.innerHTML='🎬'">` : '🎬'}</div>
      <div class="card-content"><span class="pill">${esc(type)}</span><h3>${esc(item.title || '')}</h3>
      <p>${Number(item.magnet_count || 0)} magnet link(s)${esc(season + episode)}</p><small>${esc((item.release_kind || 'release').replaceAll('_',' '))}</small></div>
    </article>`;
  }).join('');
}
async function searchItems() {
  const query = $('searchInput').value.trim().toLowerCase();
  if (!query) { $('searchStatus').textContent = 'Enter a title to search saved items.'; $('searchResultsContainer').style.display = 'block'; return; }
  const results = cachedItems.filter(x => (x.title || '').toLowerCase().includes(query) || (x.tmdb_title || '').toLowerCase().includes(query));
  $('searchStatus').textContent = `${results.length} matching saved item(s)`;
  $('searchResultsContainer').style.display = 'block';
  renderItems(results, 'searchResultsGrid');
}
async function getItemDetails(id) {
  try {
    const response = await fetch(`/api/movie/${id}`, {cache: 'no-store'});
    const item = await response.json();
    if (!response.ok) throw new Error(item.error || 'Item not found');
    $('modalTitle').textContent = item.torrent_title || 'Release details';
    const poster = item.tmdb_poster_url || item.torrent_poster_url || '';
    const magnets = (item.details || []).map(m => `<div class="magnet-row"><div><b>${esc(m.quality || 'Quality not detected')}</b>${m.file_size_bytes ? `<span> · ${(Number(m.file_size_bytes)/1073741824).toFixed(2)} GB</span>` : ''}<code>${esc(m.magnet || '')}</code></div><button class="copy-btn" onclick="copyMagnet(this)" data-magnet="${attr(m.magnet || '')}">Copy link</button></div>`).join('');
    const screenshots = (item.screenshots || []).map(src => `<a href="${attr(src)}" target="_blank" rel="noopener"><img src="${attr(src)}" alt="Screenshot" loading="lazy"></a>`).join('');
    $('modalBody').innerHTML = `${poster ? `<img class="detail-poster" src="${attr(poster)}" alt="Poster">` : ''}<p><b>Torrent title:</b> ${esc(item.torrent_title || '')}</p>${item.tmdb_title ? `<p><b>TMDB title:</b> ${esc(item.tmdb_title)}</p>` : ''}<p><b>Type:</b> ${esc(item.media_type || 'unknown')} · <b>Release:</b> ${esc(item.release_kind || 'unknown')}</p>${item.tmdb_overview ? `<p>${esc(item.tmdb_overview)}</p>` : ''}<h3>Magnet links (${(item.details || []).length})</h3>${magnets || '<p>No magnet links saved.</p>'}<h3>Screenshots</h3><div class="screenshots">${screenshots || '<p>No screenshots found.</p>'}</div><p class="source-link"><a href="${attr(item.source_url || '#')}" target="_blank" rel="noopener">Open source topic</a></p>`;
    $('movieModal').classList.add('active');
    document.body.style.overflow = 'hidden';
  } catch (err) { setStatus('error', err.message); }
}
async function copyMagnet(button) {
  try { await navigator.clipboard.writeText(button.dataset.magnet || ''); button.textContent = 'Copied'; setTimeout(() => button.textContent = 'Copy link', 1500); }
  catch { window.prompt('Copy magnet link:', button.dataset.magnet || ''); }
}
function closeModal() { $('movieModal').classList.remove('active'); document.body.style.overflow = ''; }
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function attr(value) { return esc(value).replace(/`/g, '&#96;'); }
