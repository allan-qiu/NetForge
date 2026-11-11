(() => {
  const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const inline=text=>escape(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  window.NFDocument={render(node,text){
    const lines=String(text).split('\n');let html='',code=false,table=false;
    for(const line of lines){
      if(line.trim().startsWith('```')){if(table){html+='</tbody></table></div>';table=false;}html+=code?'</code></pre>':'<pre><code>';code=!code;continue;}
      if(code){html+=escape(line)+'\n';continue;}
      if(/^\s*\|/.test(line)){
        if(/^\s*\|[\s:|\-]+$/.test(line))continue;
        if(!table){html+='<div class="document-table"><table><tbody>';table=true;}
        html+='<tr>'+line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>`<td>${inline(cell.trim())}</td>`).join('')+'</tr>';continue;
      }
      if(table){html+='</tbody></table></div>';table=false;}
      const heading=line.match(/^(#{1,6})\s+(.+)$/);
      if(heading){const level=Math.min(heading[1].length+1,6);html+=`<h${level}>${inline(heading[2])}</h${level}>`;}
      else if(/^\s*[-*]\s+/.test(line))html+=`<p class="document-list-item">• ${inline(line.replace(/^\s*[-*]\s+/,''))}</p>`;
      else if(/^---+$/.test(line.trim()))html+='<hr>';
      else if(line.trim())html+=`<p>${inline(line.replace(/^>\s?/,''))}</p>`;
    }
    if(code)html+='</code></pre>';if(table)html+='</tbody></table></div>';
    node.classList.add('rendered');node.innerHTML=html;
  }};
})();
