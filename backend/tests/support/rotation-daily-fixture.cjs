'use strict';

async function resetAndPublish(request, base, date, ok) {
  await ok(await request.post(base + '/__fixture/rotation-draft', {data: {date}}));
  const url = base + '/api/rotation-board/daily/' + date;
  const {revision} = await ok(await request.get(url));
  await ok(await request.post(url, {data: {action: 'publish', revision}}));
}

module.exports = {resetAndPublish};
