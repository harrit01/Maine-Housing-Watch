```javascript
// API Configuration
const API_BASE_URL = 'https://maine-housing-api.onrender.com'; // We'll update this after deployment
// Fetch and display meetings
async function loadMeetings() {
const meetingsList = document.getElementById('meetings-list');
try {
const response = await fetch(${API_BASE_URL}/api/meetings);
const meetings = await response.json();
if (meetings.length === 0) {
meetingsList.innerHTML = '<p class="no-results">No upcoming meetings found.</p>';
return;
}
meetingsList.innerHTML = meetings.map(meeting => `
<div class="meeting-card" data-county="${meeting.county || ''}" data-housing="${meeting.is_housing_related}">
<div class="meeting-header">
<h3>${meeting.town}</h3>
<span class="county-badge">${meeting.county || 'Unknown County'}</span>
</div>
<div class="meeting-date">
<strong>📅 ${formatDate(meeting.date)}</strong>
</div>
${meeting.is_housing_related ? '<span class="housing-badge">🏘️ Housing Related</span>' : ''}
<div class="meeting-details">
<p><strong>Board:</strong> ${meeting.board_type || 'Planning Board'}</p>
${meeting.agenda_items ? <p><strong>Agenda:</strong> ${meeting.agenda_items}</p> : ''}
</div>
${meeting.meeting_url ? <a href="${meeting.meeting_url}" target="_blank" class="meeting-link">View Details →</a> : ''}
</div>
`).join('');
applyFilters();
} catch (error) {
console.error('Error loading meetings:', error);
meetingsList.innerHTML = '<p class="error">Failed to load meetings. Please try again later.</p>';
}
}
// Fetch and display towns
async function loadTowns() {
const townsList = document.getElementById('towns-list');
try {
const response = await fetch(${API_BASE_URL}/api/towns);
const towns = await response.json();
townsList.innerHTML = towns.map(town => `
<div class="town-card">
<h3>${town.name}</h3>
<p><strong>County:</strong> ${town.county || 'Unknown'}</p>
<p><strong>Population:</strong> ${town.population ? town.population.toLocaleString() : 'N/A'}</p>
${town.website_url ? <a href="${town.website_url}" target="_blank">Town Website →</a> : ''}
</div>
`).join('');
setupTownSearch();
} catch (error) {
console.error('Error loading towns:', error);
townsList.innerHTML = '<p class="error">Failed to load towns.</p>';
}
}
// Format date helper
function formatDate(dateString) {
const date = new Date(dateString);
return date.toLocaleDateString('en-US', {
weekday: 'long',
year: 'numeric',
month: 'long',
day: 'numeric'
});
}
// Filter functionality
function applyFilters() {
const housingOnly = document.getElementById('housingOnly')?.checked;
const county = document.getElementById('countyFilter')?.value;
const cards = document.querySelectorAll('.meeting-card');
cards.forEach(card => {
const isHousing = card.dataset.housing
