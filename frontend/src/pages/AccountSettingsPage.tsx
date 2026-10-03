import React, { useState, useRef } from 'react';
import Sidebar from '../components/Sidebar';
import { useAuth } from '../context/AuthContext';

const AccountSettingsPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState('user');
  const [isEditing, setIsEditing] = useState(false);
  const [profileImage, setProfileImage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { user, signOut } = useAuth();
  const userEmail = user?.email || 'user@example.com';
  const userName = userEmail.split('@')[0];
  const userInitial = userName.charAt(0).toUpperCase();

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      const imageUrl = URL.createObjectURL(file);
      setProfileImage(imageUrl);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleRemoveImage = () => {
    setProfileImage(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const toggleEdit = () => {
    setIsEditing(!isEditing);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-groww-bg-primary">
      {/* Left Sidebar */}
      <Sidebar />

      {/* Main content area */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-8">
        <h1 className="text-2xl font-bold text-groww-text-primary mb-6">Account settings</h1>
        
        <div className="flex flex-col md:flex-row gap-6 items-start max-w-6xl">


          {/* Right Content Area */}
          <div className="flex-1 bg-white rounded-xl shadow-sm border border-gray-100 p-8 min-h-[600px] relative w-full">
            {activeTab === 'user' && (
              <div className="flex flex-col h-full">
                <div className="flex justify-between items-start mb-8">
                  <div className="flex items-center gap-6">
                    <div className="relative">
                      {/* Circular profile picture placeholder */}
                      {profileImage ? (
                        <img src={profileImage} alt="Profile" className="w-24 h-24 rounded-full object-cover shadow-md" />
                      ) : (
                        <div className="w-24 h-24 rounded-full bg-gradient-to-br from-blue-400 to-indigo-600 flex items-center justify-center text-white text-3xl font-bold shadow-md">
                          {userInitial}
                        </div>
                      )}
                      
                      <input type="file" ref={fileInputRef} onChange={handleImageUpload} accept="image/*" className="hidden" />
                      
                      {/* Upload new photo circular icon button on the bottom right */}
                      <button onClick={handleUploadClick} className="absolute bottom-0 right-0 w-8 h-8 bg-blue-700 rounded-full flex items-center justify-center text-white border-2 border-white shadow-sm hover:bg-blue-800 transition-colors">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                      </button>
                    </div>
                    <div>
                      <button onClick={handleUploadClick} className="px-5 py-2.5 bg-blue-700 text-white rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors shadow-sm">Upload New</button>
                      <button onClick={handleRemoveImage} className="px-5 py-2.5 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 transition-colors ml-3 border border-gray-200">Delete avatar</button>
                    </div>
                  </div>
                  <button onClick={toggleEdit} className="flex items-center gap-2 text-blue-700 font-semibold hover:text-blue-800 bg-[#F4F6FF] px-4 py-2 rounded-lg transition-colors">
                    {isEditing ? (
                      <>Cancel Edit</>
                    ) : (
                      <>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        Edit
                      </>
                    )}
                  </button>
                </div>
                
                {/* Form Fields */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">First Name <span className="text-red-500">*</span></label>
                    {isEditing ? (
                      <input type="text" className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm" defaultValue={userName} />
                    ) : (
                      <div className="w-full px-4 py-3 bg-white border border-transparent text-sm text-gray-800 font-medium">{userName}</div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">Last Name <span className="text-red-500">*</span></label>
                    {isEditing ? (
                      <input type="text" className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm" defaultValue="Doe" />
                    ) : (
                      <div className="w-full px-4 py-3 bg-white border border-transparent text-sm text-gray-800 font-medium">Doe</div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">Email</label>
                    {isEditing ? (
                      <input type="email" className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm text-gray-500 cursor-not-allowed" defaultValue={userEmail} readOnly />
                    ) : (
                      <div className="w-full px-4 py-3 bg-white border border-transparent text-sm text-gray-500 font-medium">{userEmail}</div>
                    )}
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">Mobile Number <span className="text-red-500">*</span></label>
                    {isEditing ? (
                      <input type="text" className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm" defaultValue="+91 9876543210" />
                    ) : (
                      <div className="w-full px-4 py-3 bg-white border border-transparent text-sm text-gray-800 font-medium">+91 9876543210</div>
                    )}
                  </div>
                </div>

                <div className="mb-8">
                  <h3 className="text-base font-bold text-gray-800 mb-4 border-b border-gray-100 pb-3">Stocks Currently Held</h3>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div className="bg-[#FAFAFA] p-4 rounded-xl border border-gray-200 flex items-center justify-between hover:border-blue-300 transition-colors">
                      <div>
                        <p className="font-bold text-gray-800">Apple (AAPL)</p>
                        <p className="text-xs text-gray-500 mt-1">12 Shares</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-green-600">+$142.50</p>
                      </div>
                    </div>
                    <div className="bg-[#FAFAFA] p-4 rounded-xl border border-gray-200 flex items-center justify-between hover:border-blue-300 transition-colors">
                      <div>
                        <p className="font-bold text-gray-800">Tesla (TSLA)</p>
                        <p className="text-xs text-gray-500 mt-1">5 Shares</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-red-500">-$24.10</p>
                      </div>
                    </div>
                    <div className="bg-[#FAFAFA] p-4 rounded-xl border border-gray-200 flex items-center justify-between hover:border-blue-300 transition-colors">
                      <div>
                        <p className="font-bold text-gray-800">Alphabet (GOOGL)</p>
                        <p className="text-xs text-gray-500 mt-1">8 Shares</p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-semibold text-green-600">+$56.20</p>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-8 pt-6 border-t border-gray-100 flex justify-between items-center w-full">
                  {isEditing ? (
                    <button onClick={() => setIsEditing(false)} className="px-8 py-3 bg-blue-700 text-white font-bold rounded-lg hover:bg-blue-800 transition-colors shadow-sm">
                      Save Changes
                    </button>
                  ) : (
                    <div></div> /* Empty div to push log out to the right if needed, or we can just let it sit. */
                  )}
                  <button onClick={signOut} className="px-8 py-3 bg-red-50 text-red-600 font-bold rounded-lg hover:bg-red-100 transition-colors ml-auto">
                    Log Out
                  </button>
                </div>
              </div>
            )}

            {activeTab === 'notifications' && (
              <div>
                <h2 className="text-xl font-bold mb-6 text-gray-800">Notifications Settings</h2>
                <div className="space-y-4">
                  <div className="p-5 bg-[#FAFAFA] rounded-xl border border-gray-200 flex justify-between items-center">
                    <div>
                      <p className="font-semibold text-gray-800">Email Notifications</p>
                      <p className="text-sm text-gray-500 mt-1">Receive alerts regarding your account activity via email.</p>
                    </div>
                    <div className="w-12 h-6 bg-blue-600 rounded-full relative cursor-pointer">
                      <div className="w-4 h-4 bg-white rounded-full absolute top-1 right-1"></div>
                    </div>
                  </div>
                  <div className="p-5 bg-[#FAFAFA] rounded-xl border border-gray-200 flex justify-between items-center">
                    <div>
                      <p className="font-semibold text-gray-800">SMS Notifications</p>
                      <p className="text-sm text-gray-500 mt-1">Get instant updates about trades and security on your phone.</p>
                    </div>
                    <div className="w-12 h-6 bg-gray-300 rounded-full relative cursor-pointer">
                      <div className="w-4 h-4 bg-white rounded-full absolute top-1 left-1"></div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'verification' && (
              <div>
                <h2 className="text-xl font-bold mb-6 text-gray-800">Verification Details</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">Aadhar Identification Number</label>
                    <input type="text" className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm" placeholder="1234 5678 9012" defaultValue="4281 9021 5521" />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-700 mb-1">Country</label>
                    <select className="w-full px-4 py-3 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm text-gray-700 appearance-none">
                      <option value="India">India</option>
                      <option value="USA">United States</option>
                      <option value="UK">United Kingdom</option>
                    </select>
                  </div>
                  <div className="md:col-span-2 mt-4">
                    <button className="px-6 py-3 bg-blue-700 text-white rounded-lg font-medium hover:bg-blue-800 transition-colors shadow-sm">
                      Save Verification Details
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
};

export default AccountSettingsPage;
