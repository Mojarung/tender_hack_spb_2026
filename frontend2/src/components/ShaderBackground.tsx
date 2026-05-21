import React, { useEffect, useRef } from 'react';

export const ShaderBackground: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const gl = canvas.getContext('webgl');
    if (!gl) return;

    // Vertex Shader: Standard Fullscreen Quad
    const vsSource = `
      attribute vec2 position;
      void main() {
        gl_Position = vec4(position, 0.0, 1.0);
      }
    `;

    // Fragment Shader: Glowing Cosmic Aurora Gradient Mesh
    const fsSource = `
      precision mediump float;
      uniform vec2 u_resolution;
      uniform float u_time;
      uniform vec2 u_mouse;

      // Simple 2D pseudo-noise
      float noise(in vec2 p) {
        return sin(p.x * 2.0) * sin(p.y * 2.0) + 0.5 * sin(p.x * 4.0 + u_time * 0.5) * cos(p.y * 3.1 + u_time * 0.3);
      }

      void main() {
        vec2 uv = gl_FragCoord.xy / u_resolution.xy;
        
        // Normalize mouse
        vec2 m = u_mouse / u_resolution.xy;
        if(u_mouse.x == 0.0 && u_mouse.y == 0.0) {
          m = vec2(0.5);
        }

        // Coordinate adjustments for organic fluid patterns
        vec2 p = uv * 3.0 - vec2(1.5);
        p.x *= u_resolution.x / u_resolution.y;

        // Animate coordinate systems
        float n1 = noise(p + vec2(u_time * 0.05, sin(u_time * 0.08)));
        float n2 = noise(p * 1.5 - vec2(u_time * 0.03, cos(u_time * 0.04)));
        
        // Blend colors based on noise waves
        float wave = sin(p.x + n1 * 2.0) * 0.5 + 0.5;
        wave = mix(wave, n2, 0.4);

        // HSL-like Premium Palette Blend (Indigo, Violet, Deep Space Blue)
        vec3 col1 = vec3(0.01, 0.02, 0.07); // Dark cosmos blue
        vec3 col2 = vec3(0.12, 0.08, 0.28); // Deep indigo
        vec3 col3 = vec3(0.25, 0.15, 0.45); // Soft neon violet
        vec3 col4 = vec3(0.05, 0.35, 0.25); // Mystical Emerald Glow (blended on interaction)

        // Mouse distance glow
        float dist = distance(uv, m);
        float mouseGlow = smoothstep(0.4, 0.0, dist) * 0.12;

        // Base blend
        vec3 finalColor = mix(col1, col2, wave);
        finalColor = mix(finalColor, col3, n1 * 0.6);
        
        // Add subtle emerald highlights on high noise values
        if (wave > 0.7) {
          finalColor = mix(finalColor, col4, (wave - 0.7) * 0.5);
        }

        // Apply mouse glow
        finalColor += col3 * mouseGlow;

        // Keep it dark and premium
        finalColor *= 0.85;

        gl_FragColor = vec4(finalColor, 1.0);
      }
    `;

    // Compile Shader Function
    const compileShader = (source: string, type: number): WebGLShader | null => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.error('Shader compilation error:', gl.getShaderInfoLog(shader));
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vs = compileShader(vsSource, gl.VERTEX_SHADER);
    const fs = compileShader(fsSource, gl.FRAGMENT_SHADER);
    if (!vs || !fs) return;

    // Create and Link Program
    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.error('Program link error:', gl.getProgramInfoLog(program));
      return;
    }

    gl.useProgram(program);

    // Setup Geometry (Fullscreen Quad)
    const positionBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
    const vertices = new Float32Array([
      -1, -1,
       1, -1,
      -1,  1,
      -1,  1,
       1, -1,
       1,  1,
    ]);
    gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);

    const positionLocation = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

    // Get Uniform Locations
    const uResolution = gl.getUniformLocation(program, 'u_resolution');
    const uTime = gl.getUniformLocation(program, 'u_time');
    const uMouse = gl.getUniformLocation(program, 'u_mouse');

    // Track mouse coordinates
    let mouseX = 0;
    let mouseY = 0;
    
    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      mouseY = rect.height - (e.clientY - rect.top); // flip Y for WebGL coords
    };

    window.addEventListener('mousemove', handleMouseMove);

    // Render loop
    let animationFrameId = 0;
    const startTime = performance.now();

    const resizeCanvas = () => {
      const displayWidth = canvas.clientWidth;
      const displayHeight = canvas.clientHeight;
      if (canvas.width !== displayWidth || canvas.height !== displayHeight) {
        canvas.width = displayWidth;
        canvas.height = displayHeight;
        gl.viewport(0, 0, canvas.width, canvas.height);
      }
    };

    const render = () => {
      resizeCanvas();
      
      const currentTime = (performance.now() - startTime) / 1000;
      
      gl.uniform2f(uResolution, canvas.width, canvas.height);
      gl.uniform1f(uTime, currentTime);
      gl.uniform2f(uMouse, mouseX, mouseY);
      
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      
      animationFrameId = requestAnimationFrame(render);
    };

    render();

    // Clean up
    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener('mousemove', handleMouseMove);
      gl.deleteBuffer(positionBuffer);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.deleteProgram(program);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100vw',
        height: '100vh',
        zIndex: -1,
        pointerEvents: 'none',
        opacity: 0.85,
      }}
    />
  );
};
