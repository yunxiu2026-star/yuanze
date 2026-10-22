import { section } from '../util.mjs';

// 纯文本页面（隐私政策、条款等）。html 字段为可信的站点作者内容，原样输出。
export default function richtext(s) {
  return section(s, `<div class="prose">${s.html || ''}</div>`);
}
